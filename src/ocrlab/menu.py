"""실행하면 뜨는 기능 선택 화면."""

from __future__ import annotations

import sys
from typing import Sequence

from .engines import ENGINES, Engine, Options

__all__ = ["choose_engines", "render_menu", "parse_selection", "SelectionError"]

#: 메뉴 한 줄이 넘치지 않도록 상태 문구를 자르는 길이
MAX_LABEL = 96


class SelectionError(ValueError):
    """선택값을 이해할 수 없을 때."""


def render_menu(options: Options) -> tuple[str, list[type[Engine]]]:
    """엔진 목록을 준비 상태와 함께 보여줄 문자열로 만든다."""
    lines = ["", "사용할 기능을 고르세요:", ""]
    available: list[type[Engine]] = list(ENGINES)

    for index, engine_class in enumerate(available, start=1):
        engine = engine_class(options)
        status = engine.check()
        mark = "○" if status.ok else "×"
        lines.append(f"  {index}) {mark} {engine_class.name}")
        lines.append(f"       {engine_class.description}")
        label = status.label()
        if len(label) > MAX_LABEL:
            label = label[: MAX_LABEL - 1] + "…"
        lines.append(f"       {'→ ' if not status.ok else ''}{label}")
        lines.append("")

    lines.append("  번호를 쉼표로 (예: 1,2,4)   a=전체   q=종료")
    return "\n".join(lines), available


def parse_selection(text: str, engines: Sequence[type[Engine]]) -> list[type[Engine]]:
    """'1,3' 또는 'a' 또는 엔진 이름을 엔진 목록으로."""
    cleaned = text.strip().lower()
    if not cleaned:
        raise SelectionError("아무것도 고르지 않았습니다")
    if cleaned in ("q", "quit", "exit", "종료"):
        return []
    if cleaned in ("a", "all", "전체", "*"):
        return list(engines)

    chosen: list[type[Engine]] = []
    by_name = {engine.name: engine for engine in engines}
    for token in cleaned.replace(" ", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if token in by_name:
            engine = by_name[token]
        else:
            try:
                index = int(token)
            except ValueError as exc:
                raise SelectionError(f"이해할 수 없는 선택: {token!r}") from exc
            if not 1 <= index <= len(engines):
                raise SelectionError(f"1~{len(engines)} 범위를 벗어났습니다: {index}")
            engine = engines[index - 1]
        if engine not in chosen:
            chosen.append(engine)

    if not chosen:
        raise SelectionError("아무것도 고르지 않았습니다")
    return chosen


def choose_engines(
    options: Options,
    *,
    stream=None,
    reader=input,
) -> list[type[Engine]]:
    """대화형 선택. 비어 있으면(=q) 종료 신호."""
    stream = stream or sys.stderr
    menu, engines = render_menu(options)
    print(menu, file=stream)

    while True:
        try:
            answer = reader("\n선택> ")
        except (EOFError, KeyboardInterrupt):
            print("", file=stream)
            return []
        try:
            return parse_selection(answer, engines)
        except SelectionError as exc:
            print(f"  {exc}. 다시 입력하세요.", file=stream)
