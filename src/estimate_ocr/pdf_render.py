"""PDF → 전처리된 PNG 이미지.

스캔본은 대비가 낮거나 색이 들뜬 경우가 많아, 그레이스케일 변환 + 대비 보정 후
모델이 감당할 수 있는 크기로 리사이즈한다.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from PIL import Image, ImageOps

try:  # PyMuPDF 1.24+ 는 `pymupdf` 가 정식 이름
    import pymupdf
except ImportError:  # pragma: no cover - 구버전 호환
    import fitz as pymupdf

log = logging.getLogger(__name__)

DEFAULT_DPI = 200
DEFAULT_MAX_EDGE = 1600


@dataclass
class RenderedPage:
    """렌더링된 페이지 한 장."""

    source: Path
    page: int  # 1-based
    image: Image.Image

    @property
    def png(self) -> bytes:
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

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


def preprocess(image: Image.Image, max_edge: int = DEFAULT_MAX_EDGE) -> Image.Image:
    """그레이스케일 → 대비 보정 → 리사이즈."""
    if image.mode != "L":
        image = image.convert("L")

    # 스캔 얼룩에 휘둘리지 않도록 상하위 1% 를 잘라내고 히스토그램을 늘린다.
    image = ImageOps.autocontrast(image, cutoff=1)

    if max_edge and max(image.size) > max_edge:
        scale = max_edge / max(image.size)
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.LANCZOS)
    return image


def render_pdf(
    path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    max_edge: int = DEFAULT_MAX_EDGE,
    pages: Sequence[int] | None = None,
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
            image = preprocess(image, max_edge=max_edge)
            log.debug("%s p%d 렌더링 완료 (%dx%d)", path.name, page_no, image.width, image.height)
            yield RenderedPage(source=path, page=page_no, image=image)


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
