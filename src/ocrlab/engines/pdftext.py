"""엔진: PDF 내장 텍스트 레이어.

OCR 도 LLM 도 쓰지 않는다. 페이지에 글자가 들어 있으면 가장 빠르고 정확하며,
스캔본이면 결과가 0건이다. **이 PDF 에 텍스트 레이어가 있는가**를 바로 알려주는
기준선(baseline) 역할을 한다.
"""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf

from ..layout import Word, extract_table
from ..schema import PageResult
from .base import Availability, Engine


class PdfTextEngine(Engine):
    name = "pdftext"
    description = "PDF 내장 텍스트 레이어 (OCR·LLM 없음, 스캔본이면 0건)"
    uses_llm = False

    def check(self) -> Availability:
        version = getattr(pymupdf, "__version__", "?")
        return Availability(ok=True, detail=f"PyMuPDF {version}")

    def extract(self, pdf: Path) -> list[PageResult]:
        results: list[PageResult] = []
        with pymupdf.open(pdf) as document:
            wanted = list(self.options.pages or range(1, document.page_count + 1))
            for number in wanted:
                if number > document.page_count:
                    continue
                started = time.perf_counter()
                page = document[number - 1]

                words = [
                    Word(text=token[4], x0=token[0], y0=token[1], x1=token[2], y1=token[3])
                    for token in page.get_text("words")
                ]
                items, fields, notes = extract_table(words)
                if not words:
                    notes.append(
                        "텍스트 레이어가 없습니다 (스캔 PDF). tesseract 나 vision 엔진을 쓰세요"
                    )

                results.append(
                    PageResult(
                        page=number,
                        items=items,
                        fields=fields,
                        raw_text=page.get_text().strip(),
                        notes=notes,
                        elapsed=time.perf_counter() - started,
                    )
                )
        return results
