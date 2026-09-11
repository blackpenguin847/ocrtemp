"""데이터 모델과 자동 검증 로직.

양식이 조금씩 다른 문서를 흡수하기 위해, 모델이 돌려준 키를 그대로 쓰지 않고
`pick()` 으로 별칭 목록과 매칭한다. 새 열 이름이 자주 등장하면
아래 `*_KEYS` 목록에 추가하면 된다. (prompts.USER_PROMPT 스키마에도 함께 추가할 것)

견적 원가 산출서는 품목 표 외에 **비목별 원가 요약**(재료비 / 노무비 / 관리비 /
포장비 / 영업이익)을 함께 싣는다. 이 요약은 `CostSummary` 로 따로 담고,
품목 행에는 `구분`(비목)을 붙여 비목별 합계와 대조한다.
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
# 합계류 비교 시 허용 오차
TOTAL_TOLERANCE = 1.0
TOTAL_TOLERANCE_RATIO = 0.01

# --------------------------------------------------------------------------
# 비목(원가 항목)
# --------------------------------------------------------------------------
#: 이 도구가 다루는 원가 비목. 순서가 곧 출력 순서다.
COST_CATEGORIES = ("재료비", "노무비", "관리비", "포장비", "영업이익")

#: 비목별 별칭. 문서마다 쓰는 말이 달라 여기서 표준 비목으로 모은다.
#: 참고: 경비/제경비는 원래 별도 비목이지만, 이 도구의 5개 비목 중에는
#: 관리비가 가장 가까워 관리비로 모은다. 분리해서 봐야 하면 이 표를 수정할 것.
CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "재료비": (
        "재료비", "재료비계", "재료비합계", "직접재료비", "간접재료비", "자재비", "재료",
        "부품비", "material", "materials", "material_cost", "raw_material",
    ),
    "노무비": (
        "노무비", "노무비계", "노무비합계", "직접노무비", "간접노무비", "인건비", "노임",
        "가공비", "labor", "labour", "labor_cost", "wage",
    ),
    "관리비": (
        "관리비", "일반관리비", "관리비계", "간접비", "경비", "제경비", "일반경비",
        "overhead", "admin", "administration", "administrative", "general_admin",
        "indirect", "expense", "expenses",
    ),
    "포장비": (
        "포장비", "포장료", "포장비계", "포장및운반비", "포장운반비",
        "packing", "packaging", "packing_cost",
    ),
    "영업이익": (
        "영업이익", "영업이익금", "이윤", "이익", "기업이윤",
        "profit", "operating_profit", "margin", "operating_income",
    ),
}

#: 원가 요약의 합계(총원가) 자리에 쓰이는 이름들
COST_TOTAL_ALIASES = (
    "합계", "총계", "총원가", "원가계", "총액", "견적금액", "공급가액", "계",
    "total", "grand_total", "sum", "total_cost",
)

_PUNCT_RE = re.compile(r"[\s_\-./\\()\[\]{}:;,'\"]+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")

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
#: 품목 행이 어느 비목에 속하는지 나타내는 열
CATEGORY_KEYS = (
    "구분", "비목", "원가구분", "비목구분", "항목구분", "분류", "원가항목", "계정",
    "category", "cost_type", "type", "division", "account",
)

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
    "소계", "합계", "총계", "총액", "공급가액합계", "금액합계",
    "subtotal", "sub_total", "total", "grand_total", "sum",
)
ITEMS_KEYS = ("품목", "품목목록", "내역", "항목", "명세", "items", "rows", "lines", "entries")
#: 비목별 원가 요약이 들어오는 자리
COSTS_KEYS = (
    "원가", "원가요약", "원가내역", "비목별원가", "원가구성", "집계", "요약",
    "costs", "cost_summary", "summary", "breakdown", "cost_breakdown",
)


def normalize_key(key: Any) -> str:
    """키 비교용 정규화: 유니코드 정규화 + 소문자 + 공백/구두점 제거."""
    text = unicodedata.normalize("NFKC", str(key))
    return _PUNCT_RE.sub("", text).lower()


#: 정규화된 별칭 → 표준 비목
_CATEGORY_LOOKUP: dict[str, str] = {
    normalize_key(alias): category
    for category, aliases in CATEGORY_ALIASES.items()
    for alias in aliases
}
_COST_TOTAL_LOOKUP = {normalize_key(alias) for alias in COST_TOTAL_ALIASES}


def normalize_category(value: Any) -> str:
    """'일반관리비', 'Labor', '재료비계' → 표준 비목. 모르는 값이면 ""."""
    if _is_empty(value):
        return ""
    return _CATEGORY_LOOKUP.get(normalize_key(value), "")


def is_cost_total_label(value: Any) -> bool:
    """'합계', '총원가' 처럼 원가 요약의 총계를 가리키는 이름인지."""
    if _is_empty(value):
        return False
    return normalize_key(value) in _COST_TOTAL_LOOKUP


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

    # 완전 일치 실패 시 부분 일치 ("금액(원)" → "금액").
    # 한 글자 키는 "재료비계"가 "계"에 걸리는 식의 오매칭을 부르므로 제외한다.
    for key in wanted:
        if len(key) < 2:
            continue
        for actual, value in normalized.items():
            if key in actual and not _is_empty(value):
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
    category: str = ""       # 표준 비목 (COST_CATEGORIES 중 하나) 또는 ""
    raw_category: str = ""   # 문서에 적힌 구분 원문

    @classmethod
    def from_raw(cls, raw: Any) -> "Item":
        if not isinstance(raw, Mapping):
            # 모델이 문자열 리스트로 돌려준 경우 품명만이라도 살린다.
            return cls(name=clean_text(raw))

        raw_category = clean_text(pick(raw, CATEGORY_KEYS))
        name = clean_text(pick(raw, NAME_KEYS))
        # 구분 열이 없는 문서에서는 품명 자체가 비목인 경우가 많다.
        category = normalize_category(raw_category) or normalize_category(name)

        return cls(
            name=name,
            spec=clean_text(pick(raw, SPEC_KEYS)),
            unit=clean_text(pick(raw, UNIT_KEYS)),
            qty=parse_number(pick(raw, QTY_KEYS)),
            unit_price=parse_number(pick(raw, UNIT_PRICE_KEYS)),
            amount=parse_number(pick(raw, AMOUNT_KEYS)),
            note=clean_text(pick(raw, NOTE_KEYS)),
            category=category,
            raw_category=raw_category,
        )

    def is_blank(self) -> bool:
        return not self.name and not self.spec and self.qty is None and self.amount is None

    def is_category_row(self) -> bool:
        """'재료비 1,000' 처럼 비목 요약 한 줄인지 (품명이 곧 비목명)."""
        return bool(normalize_category(self.name)) and self.amount is not None

    def computed_amount(self) -> float | None:
        if self.qty is None or self.unit_price is None:
            return None
        return self.qty * self.unit_price

    def effective_amount(self) -> float | None:
        return self.amount if self.amount is not None else self.computed_amount()

    def to_dict(self) -> dict[str, Any]:
        return {
            "구분": self.category or self.raw_category,
            "품명": self.name,
            "규격": self.spec,
            "단위": self.unit,
            "수량": self.qty,
            "단가": self.unit_price,
            "금액": self.amount,
            "비고": self.note,
        }


@dataclass
class CostSummary:
    """비목별 원가 요약: 재료비 / 노무비 / 관리비 / 포장비 / 영업이익."""

    values: dict[str, float | None] = field(
        default_factory=lambda: {category: None for category in COST_CATEGORIES}
    )
    total: float | None = None       # 문서에 적힌 합계(총원가)
    extras: dict[str, float] = field(default_factory=dict)  # 5개 비목에 없는 항목

    # 편의 접근자 -------------------------------------------------------
    @property
    def material(self) -> float | None:
        return self.values.get("재료비")

    @property
    def labor(self) -> float | None:
        return self.values.get("노무비")

    @property
    def overhead(self) -> float | None:
        return self.values.get("관리비")

    @property
    def packaging(self) -> float | None:
        return self.values.get("포장비")

    @property
    def profit(self) -> float | None:
        return self.values.get("영업이익")

    def get(self, category: str) -> float | None:
        return self.values.get(category)

    def set(self, category: str, amount: float | None) -> None:
        if category in self.values and amount is not None:
            self.values[category] = amount

    def is_empty(self) -> bool:
        return not self.has_categories() and self.total is None

    def has_categories(self) -> bool:
        """비목 금액이 하나라도 읽혔는지.

        합계만 있는 경우는 제외한다. 합계는 문서의 소계에서 넘어온 값일 수 있어
        "원가 요약이 있다"는 근거가 되지 못한다.
        """
        return any(value is not None for value in self.values.values())

    def parts_sum(self) -> float | None:
        """입력된 비목 금액의 합. 하나도 없으면 None."""
        present = [value for value in self.values.values() if value is not None]
        return sum(present) if present else None

    def missing(self) -> list[str]:
        return [category for category in COST_CATEGORIES if self.values[category] is None]

    @classmethod
    def from_raw(cls, raw: Any) -> "CostSummary":
        """``{"재료비": 1000, ...}`` 또는 ``[{"비목": "재료비", "금액": 1000}, ...]``."""
        summary = cls()
        if _is_empty(raw):
            return summary

        if isinstance(raw, Mapping):
            for key, value in raw.items():
                amount = parse_number(value)
                if amount is None:
                    continue
                category = normalize_category(key)
                if category:
                    summary.values[category] = amount
                elif is_cost_total_label(key):
                    summary.total = amount
                else:
                    summary.extras[clean_text(key)] = amount
            return summary

        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, Mapping):
                    continue
                label = pick(entry, CATEGORY_KEYS) or pick(entry, NAME_KEYS)
                amount = parse_number(pick(entry, AMOUNT_KEYS))
                if amount is None:
                    continue
                category = normalize_category(label)
                if category:
                    summary.values[category] = amount
                elif is_cost_total_label(label):
                    summary.total = amount
                elif not _is_empty(label):
                    summary.extras[clean_text(label)] = amount
        return summary

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {category: self.values[category] for category in COST_CATEGORIES}
        data["합계"] = self.total
        if self.extras:
            data["기타"] = dict(self.extras)
        return data


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
    costs: CostSummary = field(default_factory=CostSummary)
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

        doc.costs = CostSummary.from_raw(pick(raw, COSTS_KEYS))
        # 원가 요약 키가 없어도 최상위에 비목이 흩어져 있을 수 있다.
        if doc.costs.is_empty():
            doc.costs = CostSummary.from_raw(
                {key: value for key, value in raw.items() if normalize_category(key)}
            )

        raw_items = pick(raw, ITEMS_KEYS, default=[])
        if isinstance(raw_items, Mapping):
            raw_items = list(raw_items.values())
        if not isinstance(raw_items, list):
            raw_items = []

        items = [Item.from_raw(entry) for entry in raw_items]
        doc.items = [item for item in items if not item.is_blank()]
        doc._absorb_category_rows()

        if doc.costs.total is None and doc.subtotal is not None:
            doc.costs.total = doc.subtotal
        elif doc.subtotal is None and doc.costs.total is not None:
            doc.subtotal = doc.costs.total
        return doc

    def _absorb_category_rows(self) -> None:
        """품목 표에 섞여 들어온 비목 요약 행을 원가 요약으로 옮긴다.

        '재료비 | 1,000' 같은 행은 품목이 아니라 집계다. 세부 품목이 따로 있는
        문서에서 이런 행을 품목으로 세면 합계가 두 번 잡힌다.
        """
        detail: list[Item] = []
        for item in self.items:
            category = normalize_category(item.name)
            if category and item.amount is not None and not item.spec and item.qty in (None, 1):
                if self.costs.get(category) is None:
                    self.costs.set(category, item.amount)
                continue
            if is_cost_total_label(item.name) and item.amount is not None and not item.spec:
                if self.costs.total is None:
                    self.costs.total = item.amount
                continue
            detail.append(item)
        self.items = detail

    # -- 자동 검증 ---------------------------------------------------------
    def validate(self) -> list[Issue]:
        """검수 시트에 들어갈 경고 목록을 만든다."""
        issues: list[Issue] = []

        if self.error:
            issues.append(Issue("parse_error", f"추출 실패: {self.error}"))

        if not self.items and not self.costs.has_categories():
            if not self.error:
                issues.append(Issue("no_items", "품목을 한 건도 추출하지 못했습니다"))
            return issues

        issues.extend(self._validate_items())
        issues.extend(self._validate_costs())
        return issues

    def _validate_items(self) -> list[Issue]:
        issues: list[Issue] = []
        unknown: list[str] = []

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

            if item.raw_category and not item.category:
                unknown.append(item.raw_category)

        if unknown:
            names = ", ".join(sorted(set(unknown)))
            issues.append(
                Issue(
                    "unknown_category",
                    f"비목으로 분류하지 못한 구분값이 있습니다: {names} "
                    f"(표준 비목: {', '.join(COST_CATEGORIES)})",
                )
            )
        return issues

    def _validate_costs(self) -> list[Issue]:
        issues: list[Issue] = []

        if not self.costs.has_categories():
            # 비목별 원가 요약이 없으면 품목 합계와 소계만 비교한다.
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

        parts = self.costs.parts_sum()
        if parts is not None and self.costs.total is not None:
            if not _close_enough(parts, self.costs.total, TOTAL_TOLERANCE, TOTAL_TOLERANCE_RATIO):
                issues.append(
                    Issue(
                        "cost_total_mismatch",
                        f"비목 합계={parts:,.0f} 이지만 문서상 합계={self.costs.total:,.0f} 입니다",
                    )
                )

        missing = self.costs.missing()
        if missing and len(missing) < len(COST_CATEGORIES):
            issues.append(
                Issue("missing_category", f"원가 요약에서 읽지 못한 비목: {', '.join(missing)}")
            )

        for category, total in self.category_totals().items():
            recorded = self.costs.get(category)
            if recorded is None or total is None:
                continue
            if not _close_enough(total, recorded, TOTAL_TOLERANCE, TOTAL_TOLERANCE_RATIO):
                issues.append(
                    Issue(
                        "category_mismatch",
                        f"{category} 품목 합계={total:,.0f} 이지만 원가 요약={recorded:,.0f} 입니다",
                    )
                )

        if self.costs.extras:
            names = ", ".join(self.costs.extras)
            issues.append(
                Issue("extra_category", f"표준 비목에 없는 원가 항목이 있습니다: {names}")
            )
        return issues

    # -- 집계 --------------------------------------------------------------
    def items_total(self) -> float | None:
        values = [item.effective_amount() for item in self.items]
        values = [value for value in values if value is not None]
        return sum(values) if values else None

    def category_totals(self) -> dict[str, float]:
        """품목 행을 비목별로 합산한다 (구분이 없는 행은 제외)."""
        totals: dict[str, float] = {}
        for item in self.items:
            if not item.category:
                continue
            amount = item.effective_amount()
            if amount is None:
                continue
            totals[item.category] = totals.get(item.category, 0.0) + amount
        return totals

    def cost_total(self) -> float | None:
        """문서의 총원가. 합계가 없으면 비목 합으로 갈음한다."""
        if self.costs.total is not None:
            return self.costs.total
        parts = self.costs.parts_sum()
        if parts is not None:
            return parts
        return self.subtotal if self.subtotal is not None else self.items_total()

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
            "총원가": self.cost_total(),
            "품목합계": self.items_total(),
            "원가": self.costs.to_dict(),
            "비목별품목합계": self.category_totals(),
            "모델": self.model,
            "오류": self.error,
            "품목": [item.to_dict() for item in self.items],
            "검수": [
                {"행": issue.row, "코드": issue.code, "메시지": issue.message}
                for issue in self.validate()
            ],
        }
