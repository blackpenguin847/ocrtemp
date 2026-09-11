"""파이프라인 오케스트레이션 + fallback 재시도.

한 페이지에 대해 최대 3단계로 시도한다.

1. 전체 스키마 프롬프트 + JSON 모드
2. 표에만 집중하는 단순 프롬프트 (1단계가 실패했거나 품목이 0건일 때)
3. 원문 텍스트를 먼저 뽑고, 그 텍스트만으로 다시 구조화 (이미지 판독 자체가 흔들릴 때)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from . import prompts
from .models import Document
from .ollama_client import JSONParseError, OllamaClient, OllamaError
from .pdf_render import DEFAULT_DPI, DEFAULT_MAX_EDGE, RenderedPage, render_pdf

log = logging.getLogger(__name__)

ProgressHook = Callable[[str], None]


@dataclass
class _Attempt:
    label: str
    document: Document | None
    error: str = ""


class Extractor:
    """렌더링 → 모델 호출 → 구조화 → 검증까지 묶은 파이프라인."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        dpi: int = DEFAULT_DPI,
        max_edge: int = DEFAULT_MAX_EDGE,
        debug_dir: str | Path | None = None,
        progress: ProgressHook | None = None,
    ) -> None:
        self.client = client
        self.dpi = dpi
        self.max_edge = max_edge
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.progress = progress

    # -- 공개 API ---------------------------------------------------------
    def extract_pdf(
        self,
        path: str | Path,
        *,
        pages: Sequence[int] | None = None,
        raw_text: bool = False,
    ) -> list[Document]:
        """PDF 한 개를 페이지별 `Document` 목록으로 변환한다."""
        path = Path(path)
        documents: list[Document] = []
        for rendered in render_pdf(path, dpi=self.dpi, max_edge=self.max_edge, pages=pages):
            self._save_debug_image(rendered)
            self._notify(f"{path.name} p{rendered.page} 처리 중…")
            documents.append(
                self.extract_page_raw_text(rendered) if raw_text else self.extract_page(rendered)
            )
        return documents

    def extract_paths(
        self,
        paths: Iterable[str | Path],
        *,
        pages: Sequence[int] | None = None,
        raw_text: bool = False,
    ) -> list[Document]:
        documents: list[Document] = []
        for path in paths:
            documents.extend(self.extract_pdf(path, pages=pages, raw_text=raw_text))
        return documents

    def extract_page(self, rendered: RenderedPage) -> Document:
        """페이지 한 장을 구조화한다. 실패 시 단계적으로 fallback."""
        image = rendered.png
        attempts: list[_Attempt] = []

        attempts.append(self._try_json(rendered, prompts.USER_PROMPT, image, label="1차"))
        if not self._good(attempts[-1]):
            log.debug("%s p%d: 1차 실패 → 단순 프롬프트 재시도", rendered.source.name, rendered.page)
            self._notify(f"{rendered.source.name} p{rendered.page} 재시도(단순 프롬프트)…")
            attempts.append(self._try_json(rendered, prompts.FALLBACK_PROMPT, image, label="2차"))

        if not self._good(attempts[-1]):
            log.debug("%s p%d: 2차 실패 → 원문 추출 후 구조화", rendered.source.name, rendered.page)
            self._notify(f"{rendered.source.name} p{rendered.page} 재시도(원문→구조화)…")
            attempts.append(self._try_via_text(rendered, image, label="3차"))

        document = self._best(attempts, rendered)
        document.attempts = len(attempts)
        document.model = self.client.model
        return document

    def extract_page_raw_text(self, rendered: RenderedPage) -> Document:
        """구조화 없이 원문 텍스트만 뽑는다 (--raw-text)."""
        document = Document(
            source=rendered.source.name,
            page=rendered.page,
            model=self.client.model,
            attempts=1,
        )
        try:
            document.raw_text = self.client.generate(
                prompts.RAW_TEXT_PROMPT,
                images=[rendered.png],
                system=prompts.SYSTEM_PROMPT,
            ).strip()
        except OllamaError as exc:
            document.error = str(exc)
            log.error("%s p%d 원문 추출 실패: %s", rendered.source.name, rendered.page, exc)
        return document

    # -- 내부 -------------------------------------------------------------
    def _try_json(
        self, rendered: RenderedPage, prompt: str, image: bytes, *, label: str
    ) -> _Attempt:
        try:
            raw = self.client.generate_json(
                prompt, images=[image], system=prompts.SYSTEM_PROMPT
            )
        except JSONParseError as exc:
            log.warning("%s p%d %s: %s", rendered.source.name, rendered.page, label, exc)
            return _Attempt(label, None, str(exc))
        except OllamaError as exc:
            log.error("%s p%d %s: %s", rendered.source.name, rendered.page, label, exc)
            return _Attempt(label, None, str(exc))

        document = Document.from_raw(
            raw, source=rendered.source.name, page=rendered.page
        )
        return _Attempt(label, document, document.error)

    def _try_via_text(self, rendered: RenderedPage, image: bytes, *, label: str) -> _Attempt:
        try:
            text = self.client.generate(
                prompts.RAW_TEXT_PROMPT, images=[image], system=prompts.SYSTEM_PROMPT
            ).strip()
        except OllamaError as exc:
            return _Attempt(label, None, str(exc))

        if not text:
            return _Attempt(label, None, "원문 텍스트를 얻지 못했습니다")

        try:
            raw = self.client.generate_json(prompts.text_to_json_prompt(text))
        except (JSONParseError, OllamaError) as exc:
            document = Document(source=rendered.source.name, page=rendered.page)
            document.raw_text = text
            document.error = str(exc)
            return _Attempt(label, document, str(exc))

        document = Document.from_raw(raw, source=rendered.source.name, page=rendered.page)
        document.raw_text = text
        return _Attempt(label, document, document.error)

    @staticmethod
    def _good(attempt: _Attempt) -> bool:
        return bool(attempt.document and attempt.document.items and not attempt.document.error)

    def _best(self, attempts: list[_Attempt], rendered: RenderedPage) -> Document:
        """품목을 가장 많이 건진 시도를 채택한다."""
        candidates = [attempt for attempt in attempts if attempt.document is not None]
        if not candidates:
            reasons = "; ".join(attempt.error for attempt in attempts if attempt.error)
            return Document(
                source=rendered.source.name,
                page=rendered.page,
                error=reasons or "추출에 실패했습니다",
            )

        best = max(candidates, key=lambda attempt: len(attempt.document.items))
        document = best.document
        assert document is not None
        if not document.items and not document.error:
            document.error = "품목을 한 건도 추출하지 못했습니다"
        if document.error and best.label != attempts[0].label:
            log.debug("%s p%d: %s 결과 채택", rendered.source.name, rendered.page, best.label)
        return document

    def _save_debug_image(self, rendered: RenderedPage) -> None:
        if not self.debug_dir:
            return
        name = f"{rendered.source.stem}_p{rendered.page:03d}.png"
        path = rendered.save(self.debug_dir / name)
        log.info("전처리 이미지 저장: %s", path)

    def _notify(self, message: str) -> None:
        if self.progress:
            self.progress(message)
        log.debug(message)
