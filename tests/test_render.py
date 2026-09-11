"""렌더링과 전처리. 특히 스캔본을 망가뜨리지 않는지."""

import io

import pymupdf
import pytest
from PIL import Image, ImageChops

from estimate_ocr.pdf_render import (
    assess,
    enhance_contrast,
    fit_to_max_edge,
    preprocess,
    render_pdf,
)


def scan_like(
    *,
    ink_ratio: float,
    background: int = 231,
    ink: int = 40,
    noise: int = 8,
    white_margin: float = 0.06,
):
    """스캔본 흉내.

    실제 스캔은 종이 밝기가 한 값에 몰려 있지 않고 **노이즈로 퍼진 봉우리**를 이룬다.
    이 퍼짐이 대비 보정 버그의 방아쇠였으므로 픽스처도 그 모양을 갖춰야 한다.
    """
    width, height = 1200, 1600
    image = Image.new("L", (width, height), background)
    if noise:
        image = ImageChops.add(image, Image.effect_noise((width, height), noise), offset=-128)
    rows = max(1, int(height * ink_ratio))
    image.paste(ink, (100, 100, width - 100, 100 + rows))
    if white_margin:
        # 페이지 렌더링에서 생기는 순백 여백 (255 스파이크)
        image.paste(255, (0, 0, width, int(height * white_margin)))
    return image


def ink_ratio(image):
    histogram = image.histogram()
    return sum(histogram[:128]) / sum(histogram)


def mean(image):
    histogram = image.histogram()
    return sum(i * v for i, v in enumerate(histogram)) / sum(histogram)


def test_sparse_ink_scan_is_not_crushed():
    """회귀: 글자가 페이지의 1% 미만인 스캔본이 새까매지면 안 된다.

    양끝을 같은 비율로 잘라내는 대비 보정(autocontrast cutoff=1)은 잉크를 통째로
    잘라내고 종이만 남겨 페이지를 검게 뭉갠다. 스캔 PDF 에서 아무것도 추출되지
    않던 원인이었다.
    """
    from PIL import ImageOps

    scan = scan_like(ink_ratio=0.005)
    assert ink_ratio(scan) < 0.01          # 잉크가 1% 미만인 상황

    result = enhance_contrast(scan)

    assert mean(result) > 200              # 종이는 여전히 밝다
    assert ink_ratio(result) < 0.05        # 페이지가 검게 뒤집히지 않았다

    # 픽스처가 실제로 옛 버그를 재현하는지까지 확인한다.
    # (이 대조가 없으면 회귀 테스트가 아무것도 지키지 못한 채 통과할 수 있다)
    crushed = ImageOps.autocontrast(scan, cutoff=1)
    assert mean(crushed) < 200
    assert ink_ratio(crushed) > 0.3


def test_contrast_is_actually_improved():
    faint = scan_like(ink_ratio=0.01, background=200, ink=150)
    result = enhance_contrast(faint)

    def spread(image):
        histogram = image.histogram()
        total = sum(histogram)
        dark = next(i for i, _ in enumerate(histogram) if sum(histogram[: i + 1]) >= total * 0.002)
        paper = next(i for i, _ in enumerate(histogram) if sum(histogram[: i + 1]) >= total * 0.5)
        return paper - dark

    assert spread(result) > spread(faint) * 2


def test_blank_page_is_left_alone():
    blank = Image.new("L", (800, 1000), 240)
    assert enhance_contrast(blank) is blank        # 노이즈만 증폭하지 않는다


@pytest.mark.parametrize("background,ink", [(231, 40), (150, 20), (255, 0)])
def test_paper_stays_bright_across_exposures(background, ink):
    result = enhance_contrast(scan_like(ink_ratio=0.01, background=background, ink=ink))
    assert mean(result) > 180


def test_fit_to_max_edge_keeps_aspect_ratio():
    image = Image.new("L", (2000, 1000))
    result = fit_to_max_edge(image, 500)
    assert result.size == (500, 250)
    assert fit_to_max_edge(image, 4000) is image   # 확대는 하지 않는다


def test_assess_flags_blank_and_low_resolution():
    blank = assess(Image.new("L", (1200, 1600), 255))
    assert blank.looks_blank
    assert any("백지" in message for message in blank.warnings())

    small = assess(scan_like(ink_ratio=0.02).resize((400, 500)))
    assert small.low_resolution
    assert any("--dpi" in message for message in small.warnings())


def test_assess_flags_flooded_page():
    flooded = assess(Image.new("L", (800, 1000), 20))
    assert flooded.looks_flooded
    assert any("--no-enhance" in message for message in flooded.warnings())


# --------------------------------------------------------------------------
# 이미지 객체만 있는 스캔 PDF
# --------------------------------------------------------------------------
@pytest.fixture
def image_only_pdf(tmp_path):
    """텍스트 레이어 없이 이미지 객체 하나만 있는 PDF (스캔본의 모양)."""
    page_image = scan_like(ink_ratio=0.03, noise=0, white_margin=0).convert("RGB")
    buffer = io.BytesIO()
    page_image.save(buffer, format="JPEG", quality=70)

    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    document.save(path)
    document.close()

    check = pymupdf.open(path)
    assert check[0].get_text().strip() == ""      # 글자 없음
    assert len(check[0].get_images(full=True)) == 1
    check.close()
    return path


def test_image_only_pdf_renders_with_content(image_only_pdf):
    page = next(render_pdf(image_only_pdf, dpi=150, max_edge=1000))
    quality = page.quality()

    assert not quality.looks_blank
    assert not quality.looks_flooded
    assert quality.warnings() == []
    assert 0.001 < quality.ink_ratio < 0.5


def test_rendered_png_is_rgb(image_only_pdf):
    page = next(render_pdf(image_only_pdf, dpi=100, max_edge=800))
    assert Image.open(io.BytesIO(page.png)).mode == "RGB"


def test_enhance_can_be_turned_off(image_only_pdf):
    plain = next(render_pdf(image_only_pdf, dpi=100, max_edge=800, enhance=False))
    enhanced = next(render_pdf(image_only_pdf, dpi=100, max_edge=800, enhance=True))
    assert mean(plain.image) != mean(enhanced.image)


def test_preprocess_still_resizes():
    result = preprocess(scan_like(ink_ratio=0.02), 400)
    assert max(result.size) == 400
