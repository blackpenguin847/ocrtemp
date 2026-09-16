"""결과 저장과 비교표 출력.

엔진마다 JSON 을 하나씩 쓰고, 마지막에 지표를 나란히 놓은 비교표를 보여준다.
비교표가 이 도구의 요점이다 — 어떤 방식이 이 문서에 맞는지 한눈에 고르기 위한 것.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .schema import EngineResult

__all__ = ["write_results", "format_table", "summary_payload"]


def _width(text: str) -> int:
    """한글·한자는 터미널에서 두 칸을 차지한다."""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in str(text))


def _pad(text: str, width: int, *, align: str = "left") -> str:
    padding = max(0, width - _width(text))
    if align == "right":
        return " " * padding + str(text)
    return str(text) + " " * padding


def _number(value: float | None) -> str:
    if value is None:
        return "-"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def format_table(results: Sequence[EngineResult]) -> str:
    """엔진별 지표를 나란히 놓은 표."""
    headers = ["엔진", "상태", "소요", "페이지", "품목", "금액합계", "칸채움", "계산일치"]
    rows: list[list[str]] = []

    for result in results:
        metrics = result.metrics()
        rows.append(
            [
                result.engine,
                "OK" if result.ok else "실패",
                f"{result.elapsed:.1f}s",
                str(metrics["페이지수"]),
                str(metrics["품목수"]),
                _number(metrics["금액합계"]),
                _percent(result.fill_rate()),
                _percent(result.arithmetic_rate()),
            ]
        )

    widths = [
        max(_width(headers[index]), *(_width(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    aligns = ["left", "left", "right", "right", "right", "right", "right", "right"]

    lines = [
        "  ".join(_pad(headers[i], widths[i], align=aligns[i]) for i in range(len(headers))),
        "  ".join("─" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        lines.append(
            "  ".join(_pad(row[i], widths[i], align=aligns[i]) for i in range(len(headers)))
        )

    failures = [result for result in results if not result.ok]
    if failures:
        lines.append("")
        for result in failures:
            lines.append(f"  {result.engine} 실패: {result.error}")
    return "\n".join(lines)


def summary_payload(results: Sequence[EngineResult], pdf: str) -> dict:
    return {
        "입력": pdf,
        "실행시각": datetime.now().isoformat(timespec="seconds"),
        "엔진수": len(results),
        "엔진": {
            result.engine: {
                "성공": result.ok,
                "오류": result.error,
                "지표": result.metrics(),
                "실행조건": result.meta,
            }
            for result in results
        },
    }


def write_results(
    results: Sequence[EngineResult], output_dir: str | Path, *, pdf: str
) -> list[Path]:
    """엔진별 JSON + 비교 요약 JSON 을 쓴다."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for result in results:
        path = directory / f"{result.engine}.json"
        path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        written.append(path)

    summary = directory / "summary.json"
    summary.write_text(
        json.dumps(summary_payload(results, pdf), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written.append(summary)
    return written
