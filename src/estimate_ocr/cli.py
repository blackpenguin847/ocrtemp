"""CLI 진입점, 인자 파싱."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, prompts
from .exporter import FORMATS, export, write_raw_text
from .extractor import OCR_MODES, Extractor
from .models import Document
from .ollama_client import (
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    DEFAULT_TIMEOUT,
    OllamaClient,
    OllamaError,
)
from .pdf_render import DEFAULT_DPI, DEFAULT_MAX_EDGE, collect_pdfs, parse_pages
from .tesseract_ocr import DEFAULT_LANG, DEFAULT_PSM, TesseractError, TesseractOCR

log = logging.getLogger("estimate_ocr")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="estimate-ocr",
        description="스캔된 견적 원가명세서 PDF를 로컬 Ollama vision 모델로 구조화합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  estimate-ocr ./samples -o out\n"
            "  estimate-ocr 견적서.pdf -f all\n"
            "  estimate-ocr 견적서.pdf --pages 1,3,5-7\n"
            "  estimate-ocr ./samples --max-edge 1280 --num-ctx 4096\n"
        ),
    )
    parser.add_argument("input", help="PDF 파일 또는 PDF가 들어 있는 폴더")
    parser.add_argument("-o", "--output", default="out", help="결과 저장 폴더 (기본: out)")
    parser.add_argument(
        "-f", "--format", default="xlsx", choices=FORMATS, help="출력 형식 (기본: xlsx)"
    )
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"Ollama 모델명 (기본: {DEFAULT_MODEL})")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Ollama 서버 주소 (기본: {DEFAULT_HOST})")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"PDF 렌더링 해상도 (기본: {DEFAULT_DPI})")
    parser.add_argument(
        "--max-edge",
        type=int,
        default=DEFAULT_MAX_EDGE,
        help=f"이미지 최대 변 길이(px). VRAM 부족 시 낮출 것 (기본: {DEFAULT_MAX_EDGE})",
    )
    parser.add_argument(
        "--num-ctx", type=int, default=DEFAULT_NUM_CTX, help=f"모델 컨텍스트 길이 (기본: {DEFAULT_NUM_CTX})"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"응답 타임아웃(초) (기본: {DEFAULT_TIMEOUT})"
    )
    parser.add_argument("--pages", default=None, help="처리할 페이지. 예: 1,3,5-7 (기본: 전체)")
    parser.add_argument(
        "--raw-text", action="store_true", help="구조화 없이 원문 텍스트만 추출"
    )
    parser.add_argument(
        "--ocr",
        default="auto",
        choices=OCR_MODES,
        help=(
            "Tesseract 보조 사용법. "
            "auto=설치돼 있으면 교차검증+실패 시 대체(기본), "
            "assist=OCR 원문을 모델에 함께 제공, "
            "only=vision 없이 OCR 원문만 사용, off=끄기"
        ),
    )
    parser.add_argument(
        "--ocr-lang", default=DEFAULT_LANG, help=f"Tesseract 언어 (기본: {DEFAULT_LANG})"
    )
    parser.add_argument(
        "--ocr-psm",
        type=int,
        default=DEFAULT_PSM,
        help=f"Tesseract 페이지 분할 모드 (기본: {DEFAULT_PSM}. 표 문서는 4가 잘 맞음)",
    )
    parser.add_argument(
        "--no-enhance",
        action="store_true",
        help="대비 보정을 끄고 원본 그대로 사용 (스캔이 이미 깨끗할 때)",
    )
    parser.add_argument(
        "--debug-images", action="store_true", help="전처리된 이미지를 <output>/_debug/ 에 저장"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그 출력")
    parser.add_argument("--version", action="version", version=f"estimate-ocr {__version__}")
    return parser


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s" if not verbose else "%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def _summarize(documents: list[Document]) -> str:
    items = sum(len(document.items) for document in documents)
    warnings = sum(len(document.validate()) for document in documents)
    failed = sum(1 for document in documents if document.error)
    parts = [f"문서 {len(documents)}건", f"품목 {items}건", f"경고 {warnings}건"]
    if failed:
        parts.append(f"추출 실패 {failed}건")
    return ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)

    try:
        pdfs = collect_pdfs(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    if not pdfs:
        print(f"오류: {args.input} 안에서 PDF를 찾지 못했습니다.", file=sys.stderr)
        return 2

    try:
        pages = parse_pages(args.pages)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    client = OllamaClient(
        model=args.model,
        host=args.host,
        num_ctx=args.num_ctx,
        timeout=args.timeout,
    )

    try:
        client.ensure_model()
    except OllamaError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 3

    ocr = TesseractOCR(lang=args.ocr_lang, psm=args.ocr_psm)
    try:
        extractor = Extractor(
            client,
            dpi=args.dpi,
            max_edge=args.max_edge,
            debug_dir=output_dir / "_debug" if args.debug_images else None,
            progress=lambda message: print(message, file=sys.stderr),
            ocr=ocr,
            ocr_mode=args.ocr,
            enhance=not args.no_enhance,
        )
    except TesseractError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 4

    ocr_state = f"OCR {args.ocr}" if extractor.ocr_enabled else "OCR 미사용"
    print(
        f"PDF {len(pdfs)}개 · 모델 {args.model} · {args.host} · {ocr_state}",
        file=sys.stderr,
    )

    try:
        documents = extractor.extract_paths(pdfs, pages=pages, raw_text=args.raw_text)
    except OllamaError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("중단되었습니다.", file=sys.stderr)
        return 130

    if args.raw_text:
        written = write_raw_text(documents, output_dir)
    else:
        written = export(documents, output_dir, args.format)

    print(_summarize(documents), file=sys.stderr)
    for path in written:
        print(path)

    if not args.raw_text:
        warnings = sum(len(document.validate()) for document in documents)
        if warnings:
            print(
                f"검수 시트에 {warnings}건의 경고가 있습니다. 노란색 행을 원본과 대조하세요.",
                file=sys.stderr,
            )
        if all(document.error for document in documents):
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
