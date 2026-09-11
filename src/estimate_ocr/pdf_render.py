"""PDF → 전처리된 PNG 이미지.

스캔본은 대비가 낮거나 색이 들뜬 경우가 많아, 그레이스케일 변환 + 대비 보정 후
모델이 감당할 수 있는 크기로 리사이즈한다.

대비 보정은 문서 스캔의 히스토그램을 전제로 한다. 글자가 차지하는 면적은 보통
페이지의 1% 미만이라 `ImageOps.autocontrast` 처럼 양끝을 같은 비율로 잘라내는
방식을 쓰면 **글자 전체가 잘려나가고 종이만 남아** 페이지가 새까맣게 뭉개진다.
그래서 어두운 쪽은 아주 조금만(기본 0.2%) 자르고, 흰 기준점은 중앙값(= 종이)으로
잡아 종이를 흰색으로, 잉크를 검은색으로 보낸다.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from PIL import Image

try:  # PyMuPDF 1.24+ 는 `pymupdf` 가 정식 이름
    import pymupdf
except ImportError:  # pragma: no cover - 구버전 호환
    import fitz as pymupdf

log = logging.getLogger(__name__)

DEFAULT_DPI = 200
DEFAULT_MAX_EDGE = 1600

#: 대비 보정에서 잉크로 보고 잘라낼 어두운 쪽 비율(%). 글자 면적보다 작아야 한다.
DARK_CUTOFF_PERCENT = 0.2
#: 잉크와 종이의 밝기 차가 이보다 작으면 보정하지 않는다 (노이즈만 증폭됨)
MIN_CONTRAST_SPAN = 32
#: 이보다 어두운 픽셀을 잉크로 센다
INK_THRESHOLD = 128
#: 잉크 비율이 이보다 낮으면 백지로 의심한다
BLANK_INK_RATIO = 0.0005
#: 긴 변이 이보다 짧으면 해상도 부족으로 본다
LOW_RESOLUTION_EDGE = 1000


@dataclass
class RenderedPage:
    """렌더링된 페이지 한 장."""

    source: Path
    page: int  # 1-based
    image: Image.Image                    # vision 모델에 보낼 이미지 (max_edge 로 축소)
    full_image: Image.Image | None = None  # 축소 전 원해상도. Tesseract 는 이쪽을 쓴다

    @property
    def ocr_image(self) -> Image.Image:
        """OCR 용 이미지. 축소하면 획이 얇아져 인식률이 떨어지므로 원해상도를 쓴다."""
        return self.full_image if self.full_image is not None else self.image

    @property
    def png(self) -> bytes:
        """모델에 보낼 PNG. 1채널 이미지를 제대로 못 읽는 런타임이 있어 RGB 로 넘긴다."""
        buffer = io.BytesIO()
        self.image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def quality(self) -> "PageQuality":
        return assess(self.image)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, format="PNG")
        return path


def parse_pages(spec: str | None) -> list[int] | None:
    """``"1,3,5-7"`` → ``[1, 3, 5, 6, 7]``. None/빈 문자열이면 전체(None)."""
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None

    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk.lstrip("-"):
            start_text, _, end_text = chunk.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"페이지 범위를 이해할 수 없습니다: {chunk!r}") from exc
            if start > end:
                start, end = end, start
            pages.extend(range(start, end + 1))
        else:
            try:
                pages.append(int(chunk))
            except ValueError as exc:
                raise ValueError(f"페이지 번호를 이해할 수 없습니다: {chunk!r}") from exc

    if any(page < 1 for page in pages):
        raise ValueError("페이지 번호는 1 이상이어야 합니다")
    return sorted(dict.fromkeys(pages))


def _percentile_bin(histogram: list[int], count: int) -> int:
    """어두운 쪽부터 누적해 `count` 번째 픽셀이 속한 밝기."""
    accumulated = 0
    for value, pixels in enumerate(histogram):
        accumulated += pixels
        if accumulated >= count:
            return value
    return 255


def enhance_contrast(
    image: Image.Image,
    *,
    dark_percent: float = DARK_CUTOFF_PERCENT,
    min_span: int = MIN_CONTRAST_SPAN,
) -> Image.Image:
    """문서 스캔용 대비 보정: 잉크 → 검정, 종이 → 흰색.

    - 검은 기준점: 어두운 쪽 `dark_percent`% 지점 (글자 면적보다 작게 잡는다)
    - 흰 기준점: 밝기 중앙값. 문서 스캔에서 중앙값은 곧 종이다.

    잉크와 종이가 충분히 벌어져 있지 않으면(백지, 사진 위주 페이지) 보정하지 않고
    원본을 그대로 돌려준다. 잘못 늘리면 노이즈만 커지기 때문이다.
    """
    histogram = image.histogram()
    total = sum(histogram)
    if not total:
        return image

    black = _percentile_bin(histogram, max(1, int(total * dark_percent / 100)))
    paper = _percentile_bin(histogram, total // 2)

    if paper - black < min_span:
        return image

    scale = 255.0 / (paper - black)
    lut = [max(0, min(255, round((value - black) * scale))) for value in range(256)]
    return image.point(lut)


def fit_to_max_edge(image: Image.Image, max_edge: int) -> Image.Image:
    """긴 변이 `max_edge` 를 넘으면 비율을 지키며 줄인다."""
    if not max_edge or max(image.size) <= max_edge:
        return image
    scale = max_edge / max(image.size)
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(new_size, Image.LANCZOS)


def preprocess(
    image: Image.Image,
    max_edge: int = DEFAULT_MAX_EDGE,
    *,
    enhance: bool = True,
) -> Image.Image:
    """그레이스케일 → 대비 보정 → 리사이즈."""
    if image.mode != "L":
        image = image.convert("L")
    if enhance:
        image = enhance_contrast(image)
    return fit_to_max_edge(image, max_edge)


@dataclass
class PageQuality:
    """전처리된 이미지가 모델에 보낼 만한 상태인지."""

    width: int
    height: int
    mean: float
    ink_ratio: float          # 잉크(어두운 픽셀) 비율

    @property
    def looks_blank(self) -> bool:
        return self.ink_ratio < BLANK_INK_RATIO

    @property
    def looks_flooded(self) -> bool:
        """페이지 대부분이 어두움. 보정 실패나 스캔 불량."""
        return self.ink_ratio > 0.6

    @property
    def low_resolution(self) -> bool:
        return max(self.width, self.height) < LOW_RESOLUTION_EDGE

    def warnings(self) -> list[str]:
        messages: list[str] = []
        if self.looks_blank:
            messages.append(
                "렌더링 결과가 거의 백지입니다. PDF 가 빈 페이지이거나 렌더링에 실패했을 수 있습니다"
            )
        if self.looks_flooded:
            messages.append(
                f"페이지의 {self.ink_ratio:.0%} 가 어둡습니다. 스캔이 너무 진하면 "
                "--no-enhance 로 대비 보정을 끄고 비교해 보세요"
            )
        if self.low_resolution:
            messages.append(
                f"이미지가 작습니다({self.width}x{self.height}). --dpi 를 올리거나 "
                "--max-edge 를 키우면 글자가 또렷해집니다"
            )
        return messages


def assess(image: Image.Image) -> PageQuality:
    grayscale = image if image.mode == "L" else image.convert("L")
    histogram = grayscale.histogram()
    total = sum(histogram) or 1
    mean = sum(value * pixels for value, pixels in enumerate(histogram)) / total
    ink = sum(histogram[:INK_THRESHOLD]) / total
    return PageQuality(width=image.width, height=image.height, mean=mean, ink_ratio=ink)


def render_pdf(
    path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    max_edge: int = DEFAULT_MAX_EDGE,
    pages: Sequence[int] | None = None,
    enhance: bool = True,
) -> Iterator[RenderedPage]:
    """PDF 를 페이지별 전처리 이미지로 렌더링한다."""
    path = Path(path)
    with pymupdf.open(path) as document:
        total = document.page_count
        wanted = list(pages) if pages else list(range(1, total + 1))

        for page_no in wanted:
            if page_no > total:
                log.warning("%s: %d페이지는 존재하지 않습니다 (총 %d쪽)", path.name, page_no, total)
                continue
            pixmap = document[page_no - 1].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
            if enhance:
                image = enhance_contrast(image)

            # OCR 은 원해상도를, vision 모델은 축소본을 받는다.
            scaled = fit_to_max_edge(image, max_edge)
            rendered = RenderedPage(
                source=path,
                page=page_no,
                image=scaled,
                full_image=image if scaled is not image else None,
            )
            quality = rendered.quality()
            log.debug(
                "%s p%d 렌더링 완료 (%dx%d, 평균밝기 %.0f, 잉크 %.2f%%)",
                path.name, page_no, image.width, image.height, quality.mean, quality.ink_ratio * 100,
            )
            for message in quality.warnings():
                log.warning("%s p%d: %s", path.name, page_no, message)
            yield rendered


def collect_pdfs(target: str | Path) -> list[Path]:
    """파일 하나 또는 폴더 안의 모든 PDF 를 정렬해 반환한다."""
    target = Path(target)
    if target.is_dir():
        return sorted(p for p in target.rglob("*.pdf") if p.is_file())
    if target.is_file():
        if target.suffix.lower() != ".pdf":
            raise ValueError(f"PDF 파일이 아닙니다: {target}")
        return [target]
    raise FileNotFoundError(f"경로를 찾을 수 없습니다: {target}")
