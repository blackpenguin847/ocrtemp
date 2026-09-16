"""엔진 인터페이스.

엔진 하나 = 비교 대상 "기능" 하나. 서로 다른 방식으로 같은 PDF 를 읽고
반드시 같은 모양(`EngineResult`)으로 결과를 내놓는다.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..schema import EngineResult, PageResult

__all__ = ["Engine", "Availability", "Options"]


@dataclass
class Availability:
    """이 엔진을 지금 쓸 수 있는지."""

    ok: bool
    detail: str = ""       # 쓸 수 있을 때 보여줄 정보 (버전, 모델명 등)
    hint: str = ""         # 쓸 수 없을 때의 해결 방법

    def label(self) -> str:
        return self.detail if self.ok else (self.hint or "사용할 수 없음")


@dataclass
class Options:
    """모든 엔진이 공유하는 실행 조건. 조건이 같아야 비교가 공정하다."""

    dpi: int = 200
    max_edge: int = 1600
    enhance: bool = True
    pages: Sequence[int] | None = None

    # Ollama
    model: str = "qwen3-vl:8b"
    host: str = "http://localhost:11434"
    num_ctx: int = 8192
    timeout: int = 600

    # Tesseract
    lang: str = "kor+eng"
    psm: int = 4


class Engine(ABC):
    """엔진 공통 뼈대."""

    #: CLI 와 결과 파일에 쓰는 이름 (파일명이 되므로 소문자+하이픈)
    name: str = ""
    #: 메뉴에 한 줄로 표시할 설명
    description: str = ""
    #: 이 엔진이 LLM 을 쓰는지 (메뉴에서 소요 시간을 가늠하는 데 쓴다)
    uses_llm: bool = False

    def __init__(self, options: Options | None = None) -> None:
        self.options = options or Options()

    @abstractmethod
    def check(self) -> Availability:
        """실행 전에 준비 상태를 확인한다. 실패해도 예외를 던지지 않는다."""

    @abstractmethod
    def extract(self, pdf: Path) -> list[PageResult]:
        """페이지별 결과를 만든다. 실패는 예외로 알린다."""

    def meta(self) -> dict[str, object]:
        """결과 JSON 에 남길 실행 조건."""
        return {"dpi": self.options.dpi, "max_edge": self.options.max_edge}

    def run(self, pdf: str | Path) -> EngineResult:
        """`extract` 를 감싸 시간을 재고 실패를 결과로 바꾼다."""
        path = Path(pdf)
        result = EngineResult(engine=self.name, pdf=path.name, meta=self.meta())
        started = time.perf_counter()
        try:
            result.pages = self.extract(path)
        except Exception as exc:                      # 한 엔진의 실패가 비교 전체를 막지 않도록
            result.ok = False
            result.error = f"{type(exc).__name__}: {exc}"
        result.elapsed = time.perf_counter() - started
        return result
