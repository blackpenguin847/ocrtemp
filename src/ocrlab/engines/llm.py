"""LLM 을 쓰는 엔진 세 가지.

- ``vision``        : 이미지 → vision 모델 → JSON
- ``tesseract-llm`` : Tesseract 원문 → LLM → JSON  (텍스트 전용 모델로도 동작)
- ``vision-ocr``    : 이미지 + Tesseract 원문 → vision 모델 → JSON

셋 다 같은 스키마·같은 규칙을 주므로, 차이는 **모델에 무엇을 보여줬는가** 뿐이다.
"""

from __future__ import annotations

import time
from pathlib import Path

from .. import prompts
from ..layout import group_lines
from ..ollama import OllamaClient, OllamaError
from ..parsing import parse_document
from ..render import Page, render
from ..schema import PageResult
from .base import Availability, Engine
from .tesseract import TesseractEngine, read_words


class _LLMEngine(Engine):
    """Ollama 를 쓰는 엔진의 공통 부분."""

    uses_llm = True
    #: 이미지를 보내는 엔진인지 (vision 모델이 필요한지)
    needs_vision = True

    def client(self) -> OllamaClient:
        return OllamaClient(
            model=self.options.model,
            host=self.options.host,
            num_ctx=self.options.num_ctx,
            timeout=self.options.timeout,
        )

    def check(self) -> Availability:
        client = self.client()
        try:
            models = client.list_models()
        except OllamaError as exc:
            return Availability(ok=False, hint=str(exc))

        if models and not client.has_model():
            return Availability(
                ok=False,
                hint=f"모델 '{self.options.model}' 미설치 → ollama pull {self.options.model} "
                f"(설치됨: {', '.join(models[:4]) or '없음'})",
            )
        kind = "vision 모델 필요" if self.needs_vision else "텍스트 모델도 가능"
        return Availability(ok=True, detail=f"{self.options.model} · {kind}")

    def meta(self) -> dict[str, object]:
        return {
            **super().meta(),
            "model": self.options.model,
            "host": self.options.host,
            "num_ctx": self.options.num_ctx,
        }

    def pages(self, pdf: Path):
        return render(
            pdf,
            dpi=self.options.dpi,
            max_edge=self.options.max_edge,
            pages=self.options.pages,
            enhance=self.options.enhance,
        )

    def _ask(self, prompt: str, *, images=None) -> tuple[list, dict, list[str], str]:
        client = self.client()
        raw_text = client.generate(
            prompt, images=images, system=prompts.SYSTEM, json_mode=True
        )
        from ..ollama import loads_lenient

        parsed = loads_lenient(raw_text)
        items, fields, notes = parse_document(parsed)
        return items, fields, notes, raw_text


class VisionEngine(_LLMEngine):
    name = "vision"
    description = "이미지 → vision LLM → JSON (Ollama)"

    def extract(self, pdf: Path) -> list[PageResult]:
        results: list[PageResult] = []
        for page in self.pages(pdf):
            started = time.perf_counter()
            items, fields, notes, raw = self._ask(prompts.IMAGE_PROMPT, images=[page.png])
            results.append(
                PageResult(
                    page=page.number,
                    items=items,
                    fields=fields,
                    raw_text=raw.strip(),
                    notes=notes,
                    elapsed=time.perf_counter() - started,
                )
            )
        return results


class _OcrBackedEngine(_LLMEngine):
    """Tesseract 원문을 먼저 확보하는 엔진."""

    def check(self) -> Availability:
        ocr = TesseractEngine(self.options).check()
        if not ocr.ok:
            return ocr
        llm = super().check()
        if not llm.ok:
            return llm
        return Availability(ok=True, detail=f"{llm.detail} + {ocr.detail}")

    def meta(self) -> dict[str, object]:
        return {**super().meta(), "lang": self.options.lang, "psm": self.options.psm}

    def ocr_text(self, page: Page) -> tuple[str, float]:
        words, confidence = read_words(
            page.full_image, lang=self.options.lang, psm=self.options.psm
        )
        return "\n".join(line.text for line in group_lines(words)), confidence


class TesseractLLMEngine(_OcrBackedEngine):
    name = "tesseract-llm"
    description = "Tesseract 원문 → LLM 구조화 (이미지 안 보냄, 텍스트 모델 가능)"
    needs_vision = False

    def extract(self, pdf: Path) -> list[PageResult]:
        results: list[PageResult] = []
        for page in self.pages(pdf):
            started = time.perf_counter()
            text, confidence = self.ocr_text(page)
            notes: list[str] = []
            items: list = []
            fields: dict = {}
            if text.strip():
                items, fields, notes, _ = self._ask(prompts.text_prompt(text))
            else:
                notes.append("OCR 원문이 비었습니다")
            results.append(
                PageResult(
                    page=page.number,
                    items=items,
                    fields=fields,
                    raw_text=text,
                    confidence=confidence,
                    notes=notes,
                    elapsed=time.perf_counter() - started,
                )
            )
        return results


class VisionOcrEngine(_OcrBackedEngine):
    name = "vision-ocr"
    description = "이미지 + Tesseract 원문 → vision LLM → JSON (하이브리드)"

    def extract(self, pdf: Path) -> list[PageResult]:
        results: list[PageResult] = []
        for page in self.pages(pdf):
            started = time.perf_counter()
            text, confidence = self.ocr_text(page)
            prompt = (
                prompts.image_with_text_prompt(text) if text.strip() else prompts.IMAGE_PROMPT
            )
            items, fields, notes, raw = self._ask(prompt, images=[page.png])
            if not text.strip():
                notes.append("OCR 원문이 비어 이미지만 사용했습니다")
            results.append(
                PageResult(
                    page=page.number,
                    items=items,
                    fields=fields,
                    raw_text=raw.strip(),
                    confidence=confidence,
                    notes=notes,
                    elapsed=time.perf_counter() - started,
                )
            )
        return results
