"""엔진 등록소.

새 방식을 비교 대상에 넣으려면 `Engine` 을 상속한 클래스를 만들고
아래 `ENGINES` 에 추가하면 된다. CLI 메뉴·결과 파일·비교표는 자동으로 따라온다.
"""

from __future__ import annotations

from .base import Availability, Engine, Options
from .llm import TesseractLLMEngine, VisionEngine, VisionOcrEngine
from .pdftext import PdfTextEngine
from .tesseract import TesseractEngine

#: 메뉴에 보이는 순서 = 가벼운 것 → 무거운 것
ENGINES: tuple[type[Engine], ...] = (
    PdfTextEngine,
    TesseractEngine,
    TesseractLLMEngine,
    VisionEngine,
    VisionOcrEngine,
)

ENGINE_NAMES = tuple(engine.name for engine in ENGINES)


def get(name: str) -> type[Engine]:
    for engine in ENGINES:
        if engine.name == name:
            return engine
    raise KeyError(f"알 수 없는 엔진: {name} (가능: {', '.join(ENGINE_NAMES)})")


def build(name: str, options: Options) -> Engine:
    return get(name)(options)


__all__ = [
    "ENGINES", "ENGINE_NAMES", "Availability", "Engine", "Options",
    "PdfTextEngine", "TesseractEngine", "TesseractLLMEngine",
    "VisionEngine", "VisionOcrEngine", "build", "get",
]
