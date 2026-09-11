"""파이프라인 오케스트레이션 + fallback 재시도.

한 페이지에 대해 최대 4단계로 시도한다.

1. 전체 스키마 프롬프트 + JSON 모드
2. 표에만 집중하는 단순 프롬프트 (1단계가 실패했거나 품목이 0건일 때)
3. 원문 텍스트를 먼저 뽑고, 그 텍스트만으로 다시 구조화 (이미지 판독 자체가 흔들릴 때)
4. Tesseract 로 읽은 원문으로 구조화 (vision 경로가 전부 실패했을 때)

`ocr_mode` 로 Tesseract 보조 경로를 조절한다.

- ``off``    : 쓰지 않는다
- ``auto``   : 설치돼 있으면 페이지마다 OCR 을 돌려 **교차검증**에 쓰고, vision 이
               전부 실패하면 4단계 fallback 으로도 쓴다 (기본값)
- ``assist`` : ``auto`` 에 더해 1차 프롬프트에 OCR 원문을 함께 준다
- ``only``   : vision 을 쓰지 않고 OCR 원문만으로 구조화한다
               (vision 모델이 아예 동작하지 않을 때의 탈출구)
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
from .tesseract_ocr import TesseractError, TesseractOCR, TesseractResult

log = logging.getLogger(__name__)

ProgressHook = Callable[[str], None]

OCR_MODES = ("off", "auto", "assist", "only")


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
        ocr: TesseractOCR | None = None,
        ocr_mode: str = "auto",
        enhance: bool = True,
    ) -> None:
        if ocr_mode not in OCR_MODES:
            raise ValueError(f"알 수 없는 OCR 모드: {ocr_mode} (가능: {', '.join(OCR_MODES)})")

        self.client = client
        self.dpi = dpi
        self.max_edge = max_edge
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.progress = progress
        self.enhance = enhance
        self.ocr_mode = ocr_mode
        self.ocr = ocr if ocr is not None else (TesseractOCR() if ocr_mode != "off" else None)
        self._ocr_disabled_reason = ""

        if self.ocr_mode != "off" and self.ocr is not None:
            self._check_ocr_availability()

    def _check_ocr_availability(self) -> None:
        """Tesseract 가 없으면 조용히 끈다. 단 `only` 모드에서는 진행할 수 없다."""
        assert self.ocr is not None
        try:
            self.ocr.version()
        except TesseractError as exc:
            if self.ocr_mode == "only":
                raise
            self._ocr_disabled_reason = str(exc)
            log.info("Tesseract 보조를 끕니다: %s", exc)
            self.ocr = None
            return

        missing = self.ocr.missing_languages()
        if missing:
            log.warning(
                "Tesseract 언어 데이터가 없습니다: %s. "
                "apt install %s 로 설치하면 인식률이 올라갑니다",
                ", ".join(missing),
                " ".join(f"tesseract-ocr-{name}" for name in missing),
            )

    @property
    def ocr_enabled(self) -> bool:
        return self.ocr is not None and self.ocr_mode != "off"

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
        for rendered in render_pdf(
            path, dpi=self.dpi, max_edge=self.max_edge, pages=pages, enhance=self.enhance
        ):
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
        ocr = self._run_ocr(rendered)

        if self.ocr_mode == "only":
            document = self._extract_from_ocr(rendered, ocr)
            document.attempts = 1
            document.model = self.client.model
            self._attach_ocr(document, ocr)
            return document

        image = rendered.png
        attempts: list[_Attempt] = []

        first_prompt = prompts.USER_PROMPT
        if self.ocr_mode == "assist" and ocr is not None and ocr.is_usable():
            first_prompt = prompts.assist_prompt(ocr.text)

        attempts.append(self._try_json(rendered, first_prompt, image, label="1차"))
        if not self._good(attempts[-1]):
            log.debug("%s p%d: 1차 실패 → 단순 프롬프트 재시도", rendered.source.name, rendered.page)
            self._notify(f"{rendered.source.name} p{rendered.page} 재시도(단순 프롬프트)…")
            attempts.append(self._try_json(rendered, prompts.FALLBACK_PROMPT, image, label="2차"))

        if not self._good(attempts[-1]):
            log.debug("%s p%d: 2차 실패 → 원문 추출 후 구조화", rendered.source.name, rendered.page)
            self._notify(f"{rendered.source.name} p{rendered.page} 재시도(원문→구조화)…")
            attempts.append(self._try_via_text(rendered, image, label="3차"))

        # vision 경로가 전부 실패했으면 Tesseract 원문으로 마지막 시도
        if not self._good(attempts[-1]) and ocr is not None and ocr.is_usable():
            log.debug("%s p%d: vision 실패 → OCR 원문으로 구조화", rendered.source.name, rendered.page)
            self._notify(f"{rendered.source.name} p{rendered.page} 재시도(OCR 원문→구조화)…")
            attempts.append(self._try_from_text(rendered, ocr.text, label="4차(OCR)"))

        document = self._best(attempts, rendered)
        document.attempts = len(attempts)
        document.model = self.client.model
        self._attach_ocr(document, ocr)
        return document

    # -- Tesseract 보조 ----------------------------------------------------
    def _run_ocr(self, rendered: RenderedPage) -> TesseractResult | None:
        if not self.ocr_enabled:
            return None
        assert self.ocr is not None
        try:
            result = self.ocr.read(rendered.ocr_image)
        except TesseractError as exc:
            log.warning("%s p%d OCR 실패: %s", rendered.source.name, rendered.page, exc)
            return None
        log.debug(
            "%s p%d OCR: 단어 %d개, 신뢰도 %.1f",
            rendered.source.name, rendered.page, result.words, result.confidence,
        )
        return result

    def _attach_ocr(self, document: Document, ocr: TesseractResult | None) -> None:
        """교차검증에 쓸 OCR 결과를 문서에 붙인다.

        신뢰도가 낮은 OCR 로 대조하면 오경보만 늘어나므로, 숫자 대조는 신뢰도가
        충분할 때만 활성화한다.
        """
        if ocr is None:
            return
        document.ocr_text = ocr.text
        document.ocr_confidence = ocr.confidence
        if ocr.is_confident():
            document.ocr_numbers = set(ocr.numbers)

    def _extract_from_ocr(
        self, rendered: RenderedPage, ocr: TesseractResult | None
    ) -> Document:
        if ocr is None or not ocr.is_usable():
            return Document(
                source=rendered.source.name,
                page=rendered.page,
                error="OCR 원문을 얻지 못했습니다",
            )
        attempt = self._try_from_text(rendered, ocr.text, label="OCR")
        if attempt.document is not None:
            return attempt.document
        return Document(
            source=rendered.source.name,
            page=rendered.page,
            raw_text=ocr.text,
            error=attempt.error or "OCR 원문 구조화에 실패했습니다",
        )

    def _try_from_text(self, rendered: RenderedPage, text: str, *, label: str) -> _Attempt:
        """이미 확보한 텍스트를 LLM 으로 구조화한다 (이미지 없이)."""
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

    def extract_page_raw_text(self, rendered: RenderedPage) -> Document:
        """구조화 없이 원문 텍스트만 뽑는다 (--raw-text)."""
        document = Document(
            source=rendered.source.name,
            page=rendered.page,
            model=self.client.model,
            attempts=1,
        )
        ocr = self._run_ocr(rendered)
        self._attach_ocr(document, ocr)

        if self.ocr_mode == "only":
            document.raw_text = ocr.text if ocr else ""
            if not document.raw_text:
                document.error = "OCR 원문을 얻지 못했습니다"
            return document

        try:
            document.raw_text = self.client.generate(
                prompts.RAW_TEXT_PROMPT,
                images=[rendered.png],
                system=prompts.SYSTEM_PROMPT,
            ).strip()
        except OllamaError as exc:
            document.error = str(exc)
            log.error("%s p%d 원문 추출 실패: %s", rendered.source.name, rendered.page, exc)
            if ocr is not None and ocr.is_usable():
                document.raw_text = ocr.text
                document.error = f"{exc} (OCR 원문으로 대체)"
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
        return self._try_from_text(rendered, text, label=label)

    @staticmethod
    def _good(attempt: _Attempt) -> bool:
        """재시도가 필요 없는 결과인지.

        비목 집계만 실린 페이지(재료비/노무비/… 합계표)는 세부 품목이 0건이어도
        정상적인 추출이므로 재시도 대상이 아니다.
        """
        document = attempt.document
        if document is None or document.error:
            return False
        return bool(document.items) or document.costs.has_categories()

    @staticmethod
    def _score(document: Document) -> tuple[int, int]:
        """어떤 시도를 채택할지 비교하는 기준: 품목 수, 그다음 읽어낸 비목 수."""
        filled = sum(1 for value in document.costs.values.values() if value is not None)
        if document.costs.total is not None:
            filled += 1
        return len(document.items), filled

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

        best = max(candidates, key=lambda attempt: self._score(attempt.document))
        document = best.document
        assert document is not None
        if not document.items and not document.costs.has_categories() and not document.error:
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
