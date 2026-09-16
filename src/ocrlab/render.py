"""PDF → 이미지. 모든 이미지 기반 엔진이 같은 입력을 받도록 한 곳에 모았다."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

import pymupdf
from PIL import Image

__all__ = ["Page", "render", "page_count", "has_text_layer", "parse_pages"]

DEFAULT_DPI = 200
DEFAULT_MAX_EDGE = 1600

#: 대비 보정에서 잉크로 보고 잘라낼 어두운 쪽 비율(%)
DARK_CUTOFF_PERCENT = 0.2
#: 잉크와 종이의 밝기 차가 이보다 작으면 보정하지 않는다
MIN_CONTRAST_SPAN = 32


@dataclass
class Page:
    """렌더링된 페이지 한 장."""

    number: int                 # 1-based
    image: Image.Image          # 모델에 보낼 이미지 (max_edge 로 축소)
    full_image: Image.Image     # 축소 전 원해상도 (OCR 용)
    width_pt: float             # PDF 좌표계 폭 (텍스트 레이어 좌표와 맞추는 용도)
    height_pt: float

    @property
    def png(self) -> bytes:
        """모델에 보낼 PNG. 1채널을 못 읽는 런타임이 있어 RGB 로 넘긴다."""
        buffer = io.BytesIO()
        self.image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    def ink_ratio(self) -> float:
        histogram = self.image.convert("L").histogram()
        return sum(histogram[:128]) / (sum(histogram) or 1)


def _percentile_bin(histogram: list[int], count: int) -> int:
    accumulated = 0
    for value, pixels in enumerate(histogram):
        accumulated += pixels
        if accumulated >= count:
            return value
    return 255


def enhance_contrast(image: Image.Image) -> Image.Image:
    """문서 스캔용 대비 보정: 잉크 → 검정, 종이 → 흰색.

    어두운 쪽을 아주 조금만(0.2%) 자르고, 흰 기준점은 밝기 중앙값(= 종이)으로 잡는다.
    양끝을 같은 비율로 자르는 보정(예: autocontrast cutoff=1)은 글자가 페이지의
    1% 미만인 문서 스캔에서 **잉크를 통째로 잘라내 페이지를 검게 뭉갠다.**
    """
    histogram = image.histogram()
    total = sum(histogram)
    if not total:
        return image

    black = _percentile_bin(histogram, max(1, int(total * DARK_CUTOFF_PERCENT / 100)))
    paper = _percentile_bin(histogram, total // 2)
    if paper - black < MIN_CONTRAST_SPAN:
        return image        # 백지이거나 사진 위주 — 늘리면 노이즈만 커진다

    scale = 255.0 / (paper - black)
    lut = [max(0, min(255, round((value - black) * scale))) for value in range(256)]
    return image.point(lut)


def fit_to_max_edge(image: Image.Image, max_edge: int) -> Image.Image:
    if not max_edge or max(image.size) <= max_edge:
        return image
    scale = max_edge / max(image.size)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.LANCZOS)


def parse_pages(spec: str | None) -> list[int] | None:
    """'1,3,5-7' → [1,3,5,6,7]. 비어 있으면 None(전체)."""
    if not spec or not spec.strip():
        return None
    pages: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_text, _, end_text = chunk.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"페이지 범위를 이해할 수 없습니다: {chunk!r}") from exc
            pages.extend(range(min(start, end), max(start, end) + 1))
        else:
            try:
                pages.append(int(chunk))
            except ValueError as exc:
                raise ValueError(f"페이지 번호를 이해할 수 없습니다: {chunk!r}") from exc
    if any(page < 1 for page in pages):
        raise ValueError("페이지 번호는 1 이상이어야 합니다")
    return sorted(dict.fromkeys(pages))


def page_count(path: str | Path) -> int:
    with pymupdf.open(path) as document:
        return document.page_count


def has_text_layer(path: str | Path, *, pages: Sequence[int] | None = None) -> bool:
    """페이지에 글자가 하나라도 들어 있는지 (스캔본이면 False)."""
    with pymupdf.open(path) as document:
        wanted = pages or range(1, document.page_count + 1)
        for number in wanted:
            if number <= document.page_count and document[number - 1].get_text().strip():
                return True
    return False


def render(
    path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    max_edge: int = DEFAULT_MAX_EDGE,
    pages: Sequence[int] | None = None,
    enhance: bool = True,
) -> Iterator[Page]:
    """PDF 를 페이지별 이미지로."""
    with pymupdf.open(path) as document:
        wanted = list(pages) if pages else list(range(1, document.page_count + 1))
        for number in wanted:
            if number > document.page_count:
                continue
            source = document[number - 1]
            pixmap = source.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
            if enhance:
                image = enhance_contrast(image)
            yield Page(
                number=number,
                image=fit_to_max_edge(image, max_edge),
                full_image=image,
                width_pt=source.rect.width,
                height_pt=source.rect.height,
            )
