"""모든 엔진이 공통으로 내놓는 결과 구조.

엔진을 비교하려면 출력이 같은 모양이어야 한다. 어떤 엔진을 쓰든 결과는
`EngineResult` 하나로 모이고, JSON 도 이 구조를 그대로 직렬화한 것이다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Item", "PageResult", "EngineResult", "parse_number", "clean_text"]

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def clean_text(value: Any) -> str:
    """표시용 문자열 정리. 공백만 정돈하고 글자는 바꾸지 않는다.

    NFKC 를 쓰면 ``㎥`` → ``m3`` 처럼 단위 기호가 변해 원본 대조가 어긋나므로
    NFC 까지만 적용한다.
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def parse_number(value: Any) -> float | None:
    """'1,200', '₩ 1,200원', '(1,200)' → float. 실패하면 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace(" ", "")
    for token in ("₩", "원", "KRW", "krw"):
        text = text.replace(token, "")

    match = _NUMBER_RE.search(text)
    if not match:
        return None
    try:
        number = float(match.group())
    except ValueError:
        return None
    return -number if negative else number


@dataclass
class Item:
    """명세서의 한 행."""

    name: str = ""
    spec: str = ""
    unit: str = ""
    qty: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    note: str = ""
    category: str = ""     # 비목(재료비/노무비/…) 을 읽어낸 경우

    #: 비교용 지표 계산에 쓰는 칸 목록
    FIELDS = ("name", "spec", "unit", "qty", "unit_price", "amount", "note", "category")

    def is_empty(self) -> bool:
        return not self.name and self.qty is None and self.amount is None

    def filled(self) -> int:
        return sum(1 for name in self.FIELDS if getattr(self, name) not in (None, ""))

    def arithmetic_ok(self) -> bool | None:
        """수량 × 단가 = 금액 인지. 셋 중 하나라도 없으면 None(판정 불가)."""
        if self.qty is None or self.unit_price is None or self.amount is None:
            return None
        expected = self.qty * self.unit_price
        return abs(expected - self.amount) <= max(1.0, abs(self.amount) * 0.01)

    def to_dict(self) -> dict[str, Any]:
        return {
            "구분": self.category,
            "품명": self.name,
            "규격": self.spec,
            "단위": self.unit,
            "수량": self.qty,
            "단가": self.unit_price,
            "금액": self.amount,
            "비고": self.note,
        }


@dataclass
class PageResult:
    """페이지 한 장의 추출 결과."""

    page: int
    items: list[Item] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)   # 문서번호, 업체명, 비목별 금액 등
    raw_text: str = ""                                     # 엔진이 확보한 원문
    confidence: float | None = None                        # OCR 신뢰도 등 (없으면 None)
    notes: list[str] = field(default_factory=list)         # 경고/메모
    elapsed: float = 0.0

    def items_total(self) -> float | None:
        values = [item.amount for item in self.items if item.amount is not None]
        return sum(values) if values else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "페이지": self.page,
            "소요초": round(self.elapsed, 2),
            "신뢰도": round(self.confidence, 1) if self.confidence is not None else None,
            "필드": self.fields,
            "품목수": len(self.items),
            "금액합계": self.items_total(),
            "품목": [item.to_dict() for item in self.items],
            "메모": self.notes,
            "원문": self.raw_text,
        }


@dataclass
class EngineResult:
    """한 엔진이 PDF 하나를 처리한 전체 결과."""

    engine: str
    pdf: str
    pages: list[PageResult] = field(default_factory=list)
    elapsed: float = 0.0
    ok: bool = True
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)     # 모델명, 언어, dpi 등 실행 조건

    # -- 비교 지표 ---------------------------------------------------------
    @property
    def items(self) -> list[Item]:
        return [item for page in self.pages for item in page.items]

    def total_amount(self) -> float | None:
        values = [item.amount for item in self.items if item.amount is not None]
        return sum(values) if values else None

    def fill_rate(self) -> float | None:
        """품목 칸이 얼마나 채워졌는지 (0~1). 품목이 없으면 None."""
        items = self.items
        if not items:
            return None
        return sum(item.filled() for item in items) / (len(items) * len(Item.FIELDS))

    def arithmetic_rate(self) -> float | None:
        """수량×단가=금액 이 맞는 비율. 판정 가능한 행이 없으면 None."""
        checks = [item.arithmetic_ok() for item in self.items]
        checks = [value for value in checks if value is not None]
        if not checks:
            return None
        return sum(1 for value in checks if value) / len(checks)

    def metrics(self) -> dict[str, Any]:
        return {
            "페이지수": len(self.pages),
            "품목수": len(self.items),
            "금액합계": self.total_amount(),
            "칸채움률": round(self.fill_rate(), 3) if self.fill_rate() is not None else None,
            "계산일치율": (
                round(self.arithmetic_rate(), 3) if self.arithmetic_rate() is not None else None
            ),
            "소요초": round(self.elapsed, 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "엔진": self.engine,
            "입력": self.pdf,
            "성공": self.ok,
            "오류": self.error,
            "실행조건": self.meta,
            "지표": self.metrics(),
            "페이지": [page.to_dict() for page in self.pages],
        }
