"""ocrlab — 하나의 PDF를 여러 추출 방식으로 각각 돌려 결과를 비교하는 로컬 도구.

    from ocrlab import Options, build

    options = Options(pages=[1])
    result = build("tesseract", options).run("견적서.pdf")
    print(result.metrics())
    print(result.to_dict())
"""

from .engines import ENGINE_NAMES, ENGINES, Availability, Engine, Options, build, get
from .ollama import OllamaClient, OllamaError
from .report import format_table, write_results
from .schema import EngineResult, Item, PageResult

__version__ = "0.1.0"

__all__ = [
    "ENGINES", "ENGINE_NAMES", "Availability", "Engine", "EngineResult", "Item",
    "OllamaClient", "OllamaError", "Options", "PageResult",
    "build", "format_table", "get", "write_results", "__version__",
]
