"""진입점.

    ocrlab 견적서.pdf            # 메뉴에서 기능을 골라 실행
    ocrlab 견적서.pdf -e all     # 전부 실행 (비대화형)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .engines import ENGINE_NAMES, Engine, Options, build
from .menu import SelectionError, choose_engines, parse_selection
from .ollama import DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_NUM_CTX, DEFAULT_TIMEOUT
from .render import DEFAULT_DPI, DEFAULT_MAX_EDGE, has_text_layer, page_count, parse_pages
from .report import format_table, write_results
from .schema import EngineResult

log = logging.getLogger("ocrlab")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocrlab",
        description="하나의 PDF를 여러 추출 방식으로 각각 돌려 결과(JSON)를 비교합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "엔진:\n"
            "  pdftext        PDF 내장 텍스트 레이어 (OCR·LLM 없음)\n"
            "  tesseract      Tesseract OCR + 좌표 기반 표 재구성 (LLM 없음)\n"
            "  tesseract-llm  Tesseract 원문 → LLM 구조화\n"
            "  vision         이미지 → vision LLM\n"
            "  vision-ocr     이미지 + OCR 원문 → vision LLM\n\n"
            "예시:\n"
            "  ocrlab 견적서.pdf                    메뉴에서 고르기\n"
            "  ocrlab 견적서.pdf -e all             전부 실행\n"
            "  ocrlab 견적서.pdf -e tesseract,vision --pages 1\n"
        ),
    )
    parser.add_argument("pdf", help="입력 PDF 파일")
    parser.add_argument(
        "-e", "--engine",
        help=f"실행할 엔진 (쉼표로 여러 개, all=전체). 생략하면 메뉴가 뜹니다. "
             f"가능: {', '.join(ENGINE_NAMES)}",
    )
    parser.add_argument("-o", "--output", default="out", help="결과 저장 폴더 (기본: out)")
    parser.add_argument("--pages", default=None, help="처리할 페이지. 예: 1,3,5-7 (기본: 전체)")

    group = parser.add_argument_group("이미지 렌더링 (모든 엔진 공통)")
    group.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"기본 {DEFAULT_DPI}")
    group.add_argument(
        "--max-edge", type=int, default=DEFAULT_MAX_EDGE,
        help=f"모델에 보낼 이미지 최대 변(px). 기본 {DEFAULT_MAX_EDGE}",
    )
    group.add_argument("--no-enhance", action="store_true", help="대비 보정 끄기")

    group = parser.add_argument_group("Ollama")
    group.add_argument("-m", "--model", default=DEFAULT_MODEL, help=f"기본 {DEFAULT_MODEL}")
    group.add_argument("--host", default=DEFAULT_HOST, help=f"기본 {DEFAULT_HOST}")
    group.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help=f"기본 {DEFAULT_NUM_CTX}")
    group.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help=f"기본 {DEFAULT_TIMEOUT}초")

    group = parser.add_argument_group("Tesseract")
    group.add_argument("--lang", default="kor+eng", help="기본 kor+eng")
    group.add_argument("--psm", type=int, default=4, help="페이지 분할 모드. 기본 4 (표에 적합)")

    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그")
    parser.add_argument("--version", action="version", version=f"ocrlab {__version__}")
    return parser


def options_from_args(args: argparse.Namespace) -> Options:
    return Options(
        dpi=args.dpi,
        max_edge=args.max_edge,
        enhance=not args.no_enhance,
        pages=parse_pages(args.pages),
        model=args.model,
        host=args.host,
        num_ctx=args.num_ctx,
        timeout=args.timeout,
        lang=args.lang,
        psm=args.psm,
    )


def select_engines(args: argparse.Namespace, options: Options) -> list[type[Engine]] | None:
    """--engine 이 있으면 그대로, 없으면 메뉴. 종료를 고르면 None."""
    from .engines import ENGINES

    if args.engine:
        chosen = parse_selection(args.engine, ENGINES)
        return chosen or None

    if not sys.stdin.isatty():
        raise SelectionError(
            "대화형 선택을 쓸 수 없습니다(입력이 터미널이 아님). -e 로 엔진을 지정하세요. "
            f"예: -e all 또는 -e {ENGINE_NAMES[0]}"
        )
    return choose_engines(options) or None


def describe_input(pdf: Path, options: Options) -> str:
    pages = page_count(pdf)
    wanted = len(options.pages) if options.pages else pages
    layer = "있음" if has_text_layer(pdf, pages=options.pages) else "없음 (스캔 PDF)"
    return f"{pdf.name} · {pages}쪽 중 {wanted}쪽 처리 · 텍스트 레이어 {layer}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"오류: 파일을 찾을 수 없습니다: {pdf}", file=sys.stderr)
        return 2
    if pdf.suffix.lower() != ".pdf":
        print(f"오류: PDF 파일이 아닙니다: {pdf}", file=sys.stderr)
        return 2

    try:
        options = options_from_args(args)
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2

    try:
        print(describe_input(pdf, options), file=sys.stderr)
    except Exception as exc:
        print(f"오류: PDF 를 열지 못했습니다: {exc}", file=sys.stderr)
        return 2

    try:
        selected = select_engines(args, options)
    except (SelectionError, KeyError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    if selected is None:
        print("아무것도 실행하지 않았습니다.", file=sys.stderr)
        return 0

    results: list[EngineResult] = []
    for engine_class in selected:
        engine = build(engine_class.name, options)
        status = engine.check()
        if not status.ok:
            print(f"[{engine.name}] 건너뜀 — {status.hint}", file=sys.stderr)
            results.append(
                EngineResult(
                    engine=engine.name, pdf=pdf.name, ok=False,
                    error=f"사용할 수 없음: {status.hint}", meta=engine.meta(),
                )
            )
            continue

        print(f"[{engine.name}] 실행 중… ({status.detail})", file=sys.stderr)
        result = engine.run(pdf)
        metrics = result.metrics()
        if result.ok:
            print(
                f"[{engine.name}] 완료 — 품목 {metrics['품목수']}건, {result.elapsed:.1f}초",
                file=sys.stderr,
            )
        else:
            print(f"[{engine.name}] 실패 — {result.error}", file=sys.stderr)
        results.append(result)

    written = write_results(results, args.output, pdf=pdf.name)

    print("", file=sys.stderr)
    print(format_table(results), file=sys.stderr)
    print("", file=sys.stderr)
    for path in written:
        print(path)

    return 0 if any(result.ok for result in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
