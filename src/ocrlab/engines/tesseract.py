"""엔진: Tesseract OCR (규칙 기반 구조화).

이미지에서 단어와 좌표를 얻어, `layout` 의 규칙만으로 표를 재구성한다.
**LLM 을 쓰지 않으므로** 완전히 결정적이고 빠르다. 대신 표 구조가 복잡하거나
머리글이 깨지면 열 배정이 어긋난다.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..layout import Word, extract_table
from ..render import render
from ..schema import PageResult
from .base import Availability, Engine

#: 견적서처럼 표가 한 덩어리로 있는 문서는 psm 4(가변 크기 단일 컬럼)가 가장 잘 맞는다.
#: psm 6(균일 블록)은 표의 행을 통째로 날리는 경우가 있다.
DEFAULT_PSM = 4


def load_pytesseract():
    """pytesseract 를 불러온다. 없으면 설치 안내를 담아 던진다."""
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError(
            'pytesseract 가 없습니다. `pip install "ocrlab[tesseract]"` 를 실행하세요.'
        ) from exc
    return pytesseract


def read_words(image, *, lang: str, psm: int) -> tuple[list[Word], float]:
    """이미지에서 단어 박스와 평균 신뢰도를 얻는다."""
    pytesseract = load_pytesseract()
    data = pytesseract.image_to_data(
        image, lang=lang, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
    )

    words: list[Word] = []
    confidences: list[float] = []
    for index, text in enumerate(data.get("text", [])):
        token = (text or "").strip()
        if not token:
            continue
        try:
            confidence = float(data["conf"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            confidence = -1.0
        if confidence >= 0:
            confidences.append(confidence)
        left, top = data["left"][index], data["top"][index]
        words.append(
            Word(
                text=token,
                x0=float(left),
                y0=float(top),
                x1=float(left + data["width"][index]),
                y1=float(top + data["height"][index]),
                confidence=confidence,
            )
        )
    average = sum(confidences) / len(confidences) if confidences else 0.0
    return words, average


class TesseractEngine(Engine):
    name = "tesseract"
    description = "Tesseract OCR + 좌표 기반 표 재구성 (LLM 없음)"
    uses_llm = False

    def check(self) -> Availability:
        try:
            pytesseract = load_pytesseract()
        except RuntimeError as exc:
            return Availability(ok=False, hint=str(exc))

        try:
            version = pytesseract.get_tesseract_version()
        except Exception:
            return Availability(
                ok=False,
                hint="tesseract 실행 파일이 없습니다. "
                "apt install tesseract-ocr tesseract-ocr-kor (또는 brew install tesseract tesseract-lang)",
            )

        wanted = [name for name in self.options.lang.split("+") if name]
        try:
            installed = set(pytesseract.get_languages(config=""))
        except Exception:
            installed = set()
        missing = [name for name in wanted if installed and name not in installed]
        if missing:
            return Availability(
                ok=False,
                hint=f"언어 데이터 없음: {', '.join(missing)} → "
                + " ".join(f"apt install tesseract-ocr-{name}" for name in missing),
            )
        return Availability(ok=True, detail=f"tesseract {version} · {self.options.lang}")

    def meta(self) -> dict[str, object]:
        return {**super().meta(), "lang": self.options.lang, "psm": self.options.psm}

    def extract(self, pdf: Path) -> list[PageResult]:
        results: list[PageResult] = []
        for page in render(
            pdf,
            dpi=self.options.dpi,
            max_edge=self.options.max_edge,
            pages=self.options.pages,
            enhance=self.options.enhance,
        ):
            started = time.perf_counter()
            # OCR 은 축소 전 이미지를 쓴다. 줄이면 획이 얇아져 인식률이 떨어진다.
            words, confidence = read_words(
                page.full_image, lang=self.options.lang, psm=self.options.psm
            )
            items, fields, notes = extract_table(words)
            results.append(
                PageResult(
                    page=page.number,
                    items=items,
                    fields=fields,
                    raw_text="\n".join(
                        line.text for line in _lines(words)
                    ),
                    confidence=confidence,
                    notes=notes,
                    elapsed=time.perf_counter() - started,
                )
            )
        return results


def _lines(words: list[Word]):
    from ..layout import group_lines

    return group_lines(words)
