"""LLM 이 돌려준 JSON → 공통 스키마.

모델마다 키 이름이 조금씩 다르므로 별칭으로 흡수한다. 규칙 기반 엔진과 같은
`Item` 으로 모아야 비교가 가능하다.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .layout import match_category
from .schema import Item, clean_text, parse_number

ITEM_KEYS = ("품목", "항목", "내역", "명세", "items", "rows", "lines")
COST_KEYS = ("원가", "원가요약", "집계", "costs", "summary", "breakdown")

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "문서번호": ("문서번호", "견적번호", "관리번호", "doc_no", "number", "no"),
    "업체명": ("업체명", "거래처", "공급자", "상호", "vendor", "company", "supplier"),
    "작성일": ("작성일", "일자", "날짜", "견적일", "date"),
}
ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("품명", "품목", "명칭", "공종", "내역", "name", "item", "description"),
    "spec": ("규격", "사양", "형식", "모델", "spec", "specification"),
    "unit": ("단위", "unit"),
    "qty": ("수량", "물량", "qty", "quantity"),
    "unit_price": ("단가", "unit_price", "unitprice", "price"),
    "amount": ("금액", "공급가액", "amount", "total"),
    "note": ("비고", "적요", "note", "remark"),
    "category": ("구분", "비목", "분류", "category", "type"),
}


def _normalize(key: Any) -> str:
    return "".join(str(key).split()).lower().replace("_", "")


def pick(data: Mapping[str, Any], keys: Iterable[str]) -> Any:
    if not isinstance(data, Mapping):
        return None
    table = {}
    for raw_key, value in data.items():
        table.setdefault(_normalize(raw_key), value)

    wanted = [_normalize(key) for key in keys]
    for key in wanted:
        value = table.get(key)
        if value not in (None, "", [], {}):
            return value
    for key in wanted:
        if len(key) < 2:
            continue
        for actual, value in table.items():
            if key in actual and value not in (None, "", [], {}):
                return value
    return None


def item_from_raw(raw: Any) -> Item:
    if not isinstance(raw, Mapping):
        return Item(name=clean_text(raw))
    name = clean_text(pick(raw, ITEM_ALIASES["name"]))
    raw_category = clean_text(pick(raw, ITEM_ALIASES["category"]))
    return Item(
        name=name,
        spec=clean_text(pick(raw, ITEM_ALIASES["spec"])),
        unit=clean_text(pick(raw, ITEM_ALIASES["unit"])),
        qty=parse_number(pick(raw, ITEM_ALIASES["qty"])),
        unit_price=parse_number(pick(raw, ITEM_ALIASES["unit_price"])),
        amount=parse_number(pick(raw, ITEM_ALIASES["amount"])),
        note=clean_text(pick(raw, ITEM_ALIASES["note"])),
        category=match_category(raw_category) or match_category(name),
    )


def parse_document(raw: Any) -> tuple[list[Item], dict[str, Any], list[str]]:
    """모델 응답 → (품목, 필드, 메모)."""
    if not isinstance(raw, Mapping):
        return [], {}, ["모델 응답이 JSON 객체가 아닙니다"]

    notes: list[str] = []
    fields: dict[str, Any] = {}
    for key, aliases in FIELD_ALIASES.items():
        value = clean_text(pick(raw, aliases))
        if value:
            fields[key] = value

    costs_raw = pick(raw, COST_KEYS)
    if isinstance(costs_raw, Mapping):
        costs: dict[str, float] = {}
        for key, value in costs_raw.items():
            amount = parse_number(value)
            if amount is None:
                continue
            category = match_category(key)
            if category:
                costs[category] = amount
            elif _normalize(key) in ("합계", "총계", "총원가", "total"):
                fields["합계"] = amount
        if costs:
            fields["원가"] = costs

    raw_items = pick(raw, ITEM_KEYS) or []
    if isinstance(raw_items, Mapping):
        raw_items = list(raw_items.values())
    if not isinstance(raw_items, list):
        raw_items = []
        notes.append("품목 배열을 찾지 못했습니다")

    items = [item_from_raw(entry) for entry in raw_items]
    kept = [item for item in items if not item.is_empty()]
    if len(kept) < len(items):
        notes.append(f"빈 행 {len(items) - len(kept)}건을 버렸습니다")
    return kept, fields, notes
