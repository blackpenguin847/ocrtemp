"""테스트 공용 픽스처: 스캔본 PDF 와 디지털 PDF 를 그때그때 만든다."""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

HAS_TESSERACT = shutil.which("tesseract") is not None

#: 두 PDF 가 담고 있는 정답
ROWS = [
    ("강판", "SS400", "EA", 2, 35000),
    ("볼트", "M10", "EA", 100, 300),
    ("앵글", "L50", "M", 12, 4200),
]
ITEMS_TOTAL = sum(qty * price for *_, qty, price in ROWS)
COSTS = [("재료비", ITEMS_TOTAL), ("노무비", 400000), ("경비", 80000), ("영업이익", 150000)]
GRAND_TOTAL = ITEMS_TOTAL + sum(value for _, value in COSTS[1:])

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    pytest.skip("쓸 수 있는 트루타입 폰트가 없음")


def _page_image(size=(1700, 2200)) -> Image.Image:
    """견적서 모양의 페이지 이미지 (스캔본의 원본)."""
    font_path = _font_path()
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    title = ImageFont.truetype(font_path, 46)
    body = ImageFont.truetype(font_path, 28)

    draw.text((110, 90), "견적 원가 산출서", font=title, fill="black")
    draw.text((110, 175), "문서번호: Q-2024-001    업체명: 가나산업", font=body, fill="black")

    columns = [120, 520, 860, 1010, 1180, 1420]
    top = 280
    draw.rectangle((100, top, size[0] - 100, top + 52), outline="black", width=2)
    for x, label in zip(columns, ["품명", "규격", "단위", "수량", "단가", "금액"]):
        draw.text((x, top + 13), label, font=body, fill="black")

    for index, (name, spec, unit, qty, price) in enumerate(ROWS):
        y = top + 52 + index * 52
        draw.rectangle((100, y, size[0] - 100, y + 52), outline="black", width=1)
        cells = [name, spec, unit, str(qty), f"{price:,}", f"{qty * price:,}"]
        for x, text in zip(columns, cells):
            draw.text((x, y + 13), text, font=body, fill="black")

    y = top + 52 + len(ROWS) * 52 + 50
    for label, value in COSTS + [("합계", GRAND_TOTAL)]:
        draw.text((120, y), label, font=body, fill="black")
        draw.text((1420, y), f"{value:,}", font=body, fill="black")
        y += 46
    return image


@pytest.fixture(scope="session")
def scan_pdf(tmp_path_factory) -> Path:
    """텍스트 레이어 없이 이미지 객체만 있는 PDF (스캔본)."""
    buffer = io.BytesIO()
    # 스캔처럼 대비를 낮추고 JPEG 로 굽는다
    _page_image().point(lambda value: int(value * 0.8 + 35)).save(
        buffer, format="JPEG", quality=70
    )

    path = tmp_path_factory.mktemp("pdfs") / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buffer.getvalue())
    document.save(path)
    document.close()

    check = pymupdf.open(path)
    assert check[0].get_text().strip() == ""
    assert len(check[0].get_images(full=True)) == 1
    check.close()
    return path


@pytest.fixture(scope="session")
def digital_pdf(tmp_path_factory) -> Path:
    """진짜 텍스트 레이어가 있는 PDF."""
    path = tmp_path_factory.mktemp("pdfs") / "digital.pdf"
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_font(fontname="F0", fontfile=_font_path())

    page.insert_text((40, 45), "견적 원가 산출서", fontname="F0", fontsize=16)
    page.insert_text(
        (40, 70), "문서번호: Q-2024-001    업체명: 가나산업", fontname="F0", fontsize=9
    )
    columns = [45, 170, 290, 340, 400, 490]
    for x, label in zip(columns, ["품명", "규격", "단위", "수량", "단가", "금액"]):
        page.insert_text((x, 110), label, fontname="F0", fontsize=9)

    y = 130
    for name, spec, unit, qty, price in ROWS:
        cells = [name, spec, unit, str(qty), f"{price:,}", f"{qty * price:,}"]
        for x, text in zip(columns, cells):
            page.insert_text((x, y), text, fontname="F0", fontsize=9)
        y += 18

    y += 20
    for label, value in COSTS + [("합계", GRAND_TOTAL)]:
        page.insert_text((45, y), label, fontname="F0", fontsize=9)
        page.insert_text((490, y), f"{value:,}", fontname="F0", fontsize=9)
        y += 16

    document.save(path)
    document.close()
    return path
