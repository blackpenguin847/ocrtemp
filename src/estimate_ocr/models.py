"""데이터 모델과 자동 검증 로직.

양식이 조금씩 다른 문서를 흡수하기 위해, 모델이 돌려준 키를 그대로 쓰지 않고
`pick()` 으로 별칭 목록과 매칭한다. 새 열 이름이 자주 등장하면
아래 `*_KEYS` 목록에 추가하면 된다. (prompts.USER_PROMPT 스키마에도 함께 추가할 것)
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# 수량 × 단가 와 금액 비교 시 허용 오차 (반올림/원 단위 절사 흡수)
AMOUNT_TOLERANCE = 1.0
AMOUNT_TOLERANCE_RATIO = 0.01
# 품목 합계와 문서상 소계 비교 시 허용 오차
TOTAL_TOLERANCE = 1.0
TOTAL_TOLERANCE_RATIO = 0.01

# --------------------------------------------------------------------------
# 키 별칭 목록
# --------------------------------------------------------------------------
NAME_KEYS = (
    "품명", "품목", "품목명", "명칭", "공종", "공사명", "항목", "내역", "품목내역",
    "자재명", "규격품명", "설명",
    "name", "item", "item_name", "description", "desc", "title",
)
SPEC_KEYS = (
    "규격", "사양", "규격사양", "형식", "모델", "치수", "size",
    "spec", "specification", "model", "standard",
)
UNIT_KEYS = ("단위", "unit", "uom", "measure")
QTY_KEYS = ("수량", "물량", "개수", "qty", "quantity", "amount_qty", "count")
UNIT_PRICE_KEYS = (
    "단가", "단위단가", "재료비단가", "일위단가", "unit_price", "unitprice",
    "price", "rate",
)
AMOUNT_KEYS = (
    "금액", "합계금액", "공급가액", "계", "총액", "amount", "total", "sum",
    "line_total", "value",
)
NOTE_KEYS = ("비고", "적요", "메모", "note", "notes", "remark", "remarks", "comment")

DOC_NO_KEYS = (
    "문서번호", "견적번호", "관리번호", "번호", "문서no", "견적서번호",
    "doc_no", "document_no", "number", "no", "estimate_no", "quote_no",
)
DOC_TITLE_KEYS = ("문서명", "제목", "표제", "title", "doc_title", "document_title")
DOC_DATE_KEYS = ("작성일", "일자", "날짜", "견적일", "date", "issued_at", "doc_date")
VENDOR_KEYS = (
    "업체명", "거래처", "공급자", "수신", "회사명", "상호",
    "vendor", "supplier", "company", "customer", "client",
)
SUBTOTAL_KEYS = (
    "소계", "합계", "총계", "총액", "공급가액합계", "금액합계", "계",
    "subtotal", "sub_total", "total", "grand_total", "sum",
)
ITEMS_KEYS = ("품목", "품목목록", "내역", "항목", "명세", "items", "rows", "lines", "entries")

_PUNCT_RE = re.compile(r"[\s_\-./\\()\[\]{}:;,'\"]+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def normalize_key(key: Any) -> str:
    """키 비교용 정규화: 유니코드 정규화 + 소문자 + 공백/구두점 제거."""
    text = unicodedata.normalize("NFKC", str(key))
    return _PUNCT_RE.sub("", text).lower()


def pick(data: Mapping[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    """`keys` 중 먼저 발견되는 비어있지 않은 값을 반환한다.

    키는 정규화 후 비교하므로 ``"단 가"``, ``"unit_price"``, ``"UnitPrice"`` 가
    모두 같은 키로 취급된다. 완전 일치가 없으면 부분 일치(포함)까지 시도한다.
    """
    if not isinstance(data, Mapping):
        return default

    normalized = {}
    for raw_key, value in data.items():
        normalized.setdefault(normalize_key(raw_key), value)

    wanted = [normalize_key(k) for k in keys]

    for key in wanted:
        value = normalized.get(key)
        if not _is_empty(value):
            return value

    # 완전 일치 실패 시 부분 일치 ("금액(원)" → "금액")
    for key in wanted:
        for actual, value in normalized.items():
            if key and key in actual and not _is_empty(value):
                return value
    return default


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ("", "-", "—", "n/a", "N/A", "null", "None")
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def parse_number(value: Any) -> float | None:
    """'1,200', '₩ 1,200원', '(1,200)', 1200.0 → float. 실패 시 None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)

    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace(",", "").replace(" ", "")
    for token in ("₩", "원", "KRW", "krw", "won"):
        text = text.replace(token, "")

    match = _NUM_RE.search(text)
    if not match:
        return None
    try:
        number = float(match.group())
    except ValueError:
        return None
    return -number if negative else number


def clean_text(value: Any) -> str:
    """표시용 문자열 정리. 공백만 정돈하고 글자는 건드리지 않는다.

    여기서 NFKC 를 쓰면 ``㎥`` → ``m3``, ``㎡`` → ``m2`` 처럼 단위 기호가
    바뀌어 원본과 대조가 어려워지므로 NFC 까지만 적용한다.
    """
    if _is_empty(value):
        return ""
    text = unicodedata.normalize("NFC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def _close_enough(left: float, right: float, tol: float, ratio: float) -> bool:
    return abs(left - right) <= max(tol, abs(right) * ratio, abs(left) * ratio)


# --------------------------------------------------------------------------
# 모델
# --------------------------------------------------------------------------
@dataclass
class Issue:
    """자동 검증에서 걸린 경고 한 건."""

    code: str
    message: str
    row: int | None = None  # 1-based 품목 번호. None 이면 문서 단위 경고

    def __str__(self) -> str:
        where = f"{self.row}행" if self.row else "문서"
        return f"[{where}] {self.message}"


@dataclass
class Item:
    """명세서의 한 품목(= 결과 시트의 한 행)."""

    name: str = ""
    spec: str = ""
    unit: str = ""
    qty: float | None = None
    unit_price: float | None = None
    amount: float | None = None
    note: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "Item":
        if not isinstance(raw, Mapping):
            # 모델이 문자열 리스트로 돌려준 경우 품명만이라도 살린다.
            return cls(name=clean_text(raw))
        return cls(
            name=clean_text(pick(raw, NAME_KEYS)),
            spec=clean_text(pick(raw, SPEC_KEYS)),
            unit=clean_text(pick(raw, UNIT_KEYS)),
            qty=parse_number(pick(raw, QTY_KEYS)),
            unit_price=parse_number(pick(raw, UNIT_PRICE_KEYS)),
            amount=parse_number(pick(raw, AMOUNT_KEYS)),
            note=clean_text(pick(raw, NOTE_KEYS)),
        )

    def is_blank(self) -> bool:
        return not self.name and not self.spec and self.qty is None and self.amount is None

    def computed_amount(self) -> float | None:
        if self.qty is None or self.unit_price is None:
            return None
        return self.qty * self.unit_price

    def effective_amount(self) -> float | None:
        return self.amount if self.amount is not None else self.computed_amount()

    def to_dict(self) -> dict[str, Any]:
        return {
            "품명": self.name,
            "규격": self.spec,
            "단위": self.unit,
            "수량": self.qty,
            "단가": self.unit_price,
            "금액": self.amount,
            "비고": self.note,
        }


@dataclass
class Document:
    """PDF 한 페이지에서 추출한 명세서 하나."""

    source: str = ""          # 원본 PDF 파일명
    page: int = 0             # 1-based 페이지 번호
    doc_no: str = ""          # 문서번호
    title: str = ""
    date: str = ""
    vendor: str = ""
    subtotal: float | None = None   # 문서에 적힌 소계/합계
    items: list[Item] = field(default_factory=list)
    raw_text: str = ""        # --raw-text 또는 fallback 과정에서 얻은 원문
    error: str = ""           # JSON 파싱 실패 등 치명적 오류 메시지
    model: str = ""
    attempts: int = 0

    @classmethod
    def from_raw(cls, raw: Any, *, source: str = "", page: int = 0) -> "Document":
        doc = cls(source=source, page=page)
        if not isinstance(raw, Mapping):
            doc.error = "모델 응답이 JSON 객체가 아님"
            return doc

        doc.doc_no = clean_text(pick(raw, DOC_NO_KEYS))
        doc.title = clean_text(pick(raw, DOC_TITLE_KEYS))
        doc.date = clean_text(pick(raw, DOC_DATE_KEYS))
        doc.vendor = clean_text(pick(raw, VENDOR_KEYS))
        doc.subtotal = parse_number(pick(raw, SUBTOTAL_KEYS))

        raw_items = pick(raw, ITEMS_KEYS, default=[])
        if isinstance(raw_items, Mapping):
            raw_items = list(raw_items.values())
        if not isinstance(raw_items, list):
            raw_items = []

        items = [Item.from_raw(entry) for entry in raw_items]
        doc.items = [item for item in items if not item.is_blank()]
        return doc

    # -- 자동 검증 ---------------------------------------------------------
    def validate(self) -> list[Issue]:
        """검수 시트에 들어갈 경고 목록을 만든다."""
        issues: list[Issue] = []

        if self.error:
            issues.append(Issue("parse_error", f"추출 실패: {self.error}"))

        if not self.items:
            if not self.error:
                issues.append(Issue("no_items", "품목을 한 건도 추출하지 못했습니다"))
            return issues

        for index, item in enumerate(self.items, start=1):
            if not item.name:
                issues.append(Issue("empty_name", "품명이 비어 있습니다", row=index))

            computed = item.computed_amount()
            if computed is not None and item.amount is not None:
                if not _close_enough(computed, item.amount, AMOUNT_TOLERANCE, AMOUNT_TOLERANCE_RATIO):
                    issues.append(
                        Issue(
                            "amount_mismatch",
                            f"수량×단가={computed:,.0f} 이지만 금액={item.amount:,.0f} 입니다",
                            row=index,
                        )
                    )
            elif item.amount is None and computed is None:
                issues.append(Issue("missing_amount", "금액을 읽지 못했습니다", row=index))

        if self.subtotal is not None:
            total = self.items_total()
            if total is not None and not _close_enough(
                total, self.subtotal, TOTAL_TOLERANCE, TOTAL_TOLERANCE_RATIO
            ):
                issues.append(
                    Issue(
                        "subtotal_mismatch",
                        f"품목 금액 합계={total:,.0f} 이지만 문서상 소계={self.subtotal:,.0f} 입니다",
                    )
                )
        return issues

    def items_total(self) -> float | None:
        values = [item.effective_amount() for item in self.items]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    def is_ok(self) -> bool:
        return not self.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "원본": self.source,
            "페이지": self.page,
            "문서번호": self.doc_no,
            "문서명": self.title,
            "작성일": self.date,
            "업체명": self.vendor,
            "소계": self.subtotal,
            "품목합계": self.items_total(),
            "모델": self.model,
            "오류": self.error,
            "품목": [item.to_dict() for item in self.items],
            "검수": [
                {"행": issue.row, "코드": issue.code, "메시지": issue.message}
                for issue in self.validate()
            ],
        }
