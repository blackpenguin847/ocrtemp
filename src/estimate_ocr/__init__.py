"""estimate-ocr: 스캔된 견적 원가명세서 PDF → 구조화 데이터.

    from estimate_ocr import OllamaClient, Extractor

    client = OllamaClient(model="qwen3-vl:8b")
    docs = Extractor(client).extract_pdf("견적서.pdf")

    for doc in docs:
        print(doc.page, len(doc.items), doc.validate())
"""

from .exporter import export
from .extractor import Extractor
from .models import Document, Issue, Item
from .ollama_client import JSONParseError, OllamaClient, OllamaError
from .pdf_render import RenderedPage, collect_pdfs, parse_pages, render_pdf

__version__ = "0.1.0"

__all__ = [
    "Document",
    "Extractor",
    "Issue",
    "Item",
    "JSONParseError",
    "OllamaClient",
    "OllamaError",
    "RenderedPage",
    "collect_pdfs",
    "export",
    "parse_pages",
    "render_pdf",
    "__version__",
]
