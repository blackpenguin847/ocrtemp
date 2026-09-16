"""단어 박스 → 표 → 품목.

PDF 텍스트 레이어와 Tesseract 는 둘 다 "글자 + 좌표" 를 준다. 그 뒤의 일
(줄로 묶고, 열을 나누고, 칸을 필드에 배정하는 일)은 완전히 같으므로 여기 모았다.
덕분에 두 엔진의 차이는 **박스를 어디서 얻었는가** 하나로 좁혀지고, 비교가 공정해진다.

LLM 을 전혀 쓰지 않는 규칙 기반 경로다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .schema import Item, clean_text, parse_number

__all__ = ["Word", "Line", "Header", "group_lines", "detect_header", "extract_table"]

# --------------------------------------------------------------------------
# 열 이름 별칭
# --------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("품명", "품목", "품목명", "공종", "명칭", "내역", "항목", "자재명",
             "name", "item", "description"),
    "spec": ("규격", "사양", "형식", "모델", "치수", "spec", "size", "model"),
    "unit": ("단위", "unit"),
    "qty": ("수량", "물량", "개수", "qty", "quantity"),
    "unit_price": ("단가", "단위단가", "일위단가", "unitprice", "price", "rate"),
    "amount": ("금액", "공급가액", "합계금액", "amount", "total"),
    "note": ("비고", "적요", "메모", "note", "remark"),
}

#: 비목 집계 행. 품목이 아니라 문서 필드로 뺀다.
CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "재료비": ("재료비", "자재비", "직접재료비", "간접재료비", "재료비계"),
    "노무비": ("노무비", "인건비", "노임", "직접노무비", "간접노무비", "노무비계"),
    "경비": ("경비", "제경비", "직접경비", "간접경비", "경비계"),
    "관리비": ("관리비", "일반관리비", "간접비", "관리비계"),
    "포장비": ("포장비", "포장료", "포장및운반비"),
    "영업이익": ("영업이익", "이윤", "이익", "기업이윤"),
}
TOTAL_LABELS = ("합계", "총계", "총액", "총원가", "원가계", "견적금액", "소계")

#: 문서 머리말에서 뽑는 값
DOC_NO_LABELS = ("문서번호", "견적번호", "관리번호", "견적서번호", "번호")
VENDOR_LABELS = ("업체명", "업체", "거래처", "공급자", "수신", "상호", "회사명")
DATE_LABELS = ("작성일", "견적일", "일자", "날짜")

_SPACE_RE = re.compile(r"\s+")
_NUMERIC_RE = re.compile(r"^[\d,.\s]+$")


def _normalize(text: str) -> str:
    return _SPACE_RE.sub("", str(text)).lower()


_COLUMN_LOOKUP = {
    _normalize(alias): column
    for column, aliases in COLUMN_ALIASES.items()
    for alias in aliases
}
_CATEGORY_LOOKUP = {
    _normalize(alias): category
    for category, aliases in CATEGORY_ALIASES.items()
    for alias in aliases
}


def match_column(text: str) -> str:
    """'금액(원)' → 'amount'. 못 찾으면 ''."""
    key = _normalize(text).strip("()[]:")
    if key in _COLUMN_LOOKUP:
        return _COLUMN_LOOKUP[key]
    for alias, column in _COLUMN_LOOKUP.items():
        if alias and alias in key:
            return column
    return ""


def match_category(text: str) -> str:
    """'일반관리비' → '관리비'. 못 찾으면 ''."""
    return _CATEGORY_LOOKUP.get(_normalize(text).strip("()[]:"), "")


def is_total_label(text: str) -> bool:
    return _normalize(text).strip("()[]:") in {_normalize(label) for label in TOTAL_LABELS}


# --------------------------------------------------------------------------
# 기본 자료구조
# --------------------------------------------------------------------------
@dataclass
class Word:
    """한 단어와 그 위치."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float | None = None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)


@dataclass
class Line:
    """같은 줄에 있는 단어들."""

    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def y0(self) -> float:
        return min(word.y0 for word in self.words)

    @property
    def y1(self) -> float:
        return max(word.y1 for word in self.words)

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Header:
    """표 머리글: 어떤 열이 가로로 어디쯤 있는지."""

    line_index: int
    columns: list[tuple[str, float]]        # (필드명, 중심 x)

    def boundaries(self) -> list[tuple[str, float, float]]:
        """인접한 열 중심의 중간을 경계로 삼는다."""
        ordered = sorted(self.columns, key=lambda pair: pair[1])
        spans: list[tuple[str, float, float]] = []
        for index, (name, center) in enumerate(ordered):
            left = float("-inf") if index == 0 else (ordered[index - 1][1] + center) / 2
            right = float("inf") if index == len(ordered) - 1 else (ordered[index + 1][1] + center) / 2
            spans.append((name, left, right))
        return spans

    def assign(self, line: Line) -> dict[str, str]:
        """줄의 단어들을 열별로 나눈다."""
        buckets: dict[str, list[str]] = {}
        for name, left, right in self.boundaries():
            for word in line.words:
                if left <= word.cx < right:
                    buckets.setdefault(name, []).append(word.text)
        return {name: " ".join(parts) for name, parts in buckets.items()}


# --------------------------------------------------------------------------
# 줄 묶기 / 머리글 찾기
# --------------------------------------------------------------------------
def group_lines(words: list[Word], *, overlap: float = 0.4) -> list[Line]:
    """세로로 겹치는 단어들을 한 줄로 묶는다."""
    if not words:
        return []

    ordered = sorted(words, key=lambda word: (word.cy, word.x0))
    lines: list[Line] = []
    current: list[Word] = [ordered[0]]
    current_top, current_bottom = ordered[0].y0, ordered[0].y1

    for word in ordered[1:]:
        height = min(word.height, current_bottom - current_top or word.height)
        shared = min(word.y1, current_bottom) - max(word.y0, current_top)
        if shared >= height * overlap:
            current.append(word)
            current_top = min(current_top, word.y0)
            current_bottom = max(current_bottom, word.y1)
        else:
            lines.append(Line(sorted(current, key=lambda item: item.x0)))
            current = [word]
            current_top, current_bottom = word.y0, word.y1

    lines.append(Line(sorted(current, key=lambda item: item.x0)))
    return lines


def detect_header(lines: list[Line], *, minimum: int = 3) -> Header | None:
    """열 이름이 `minimum` 개 이상 모여 있는 줄을 표 머리글로 본다."""
    best: Header | None = None
    for index, line in enumerate(lines):
        columns: list[tuple[str, float]] = []
        seen: set[str] = set()
        for word in line.words:
            column = match_column(word.text)
            if column and column not in seen:
                seen.add(column)
                columns.append((column, word.cx))
        if len(columns) >= minimum and (best is None or len(columns) > len(best.columns)):
            best = Header(line_index=index, columns=columns)
    return best


# --------------------------------------------------------------------------
# 줄 → 품목
# --------------------------------------------------------------------------
def item_from_columns(cells: dict[str, str]) -> Item:
    return Item(
        name=clean_text(cells.get("name")),
        spec=clean_text(cells.get("spec")),
        unit=clean_text(cells.get("unit")),
        qty=parse_number(cells.get("qty")),
        unit_price=parse_number(cells.get("unit_price")),
        amount=parse_number(cells.get("amount")),
        note=clean_text(cells.get("note")),
    )


def item_from_text(text: str) -> Item | None:
    """머리글을 못 찾았을 때: 줄 끝의 숫자를 금액·단가·수량으로 읽는다.

    '강판 SS400 3.2T 2 35,000 70,000' 처럼 견적서 한 행은 오른쪽이 숫자로 끝난다.
    오른쪽부터 금액 → 단가 → 수량 순으로 집고, 남은 앞부분을 품명/규격으로 본다.
    """
    tokens = text.split()
    if len(tokens) < 2:
        return None

    trailing: list[float] = []
    while tokens and len(trailing) < 3:
        value = parse_number(tokens[-1]) if _NUMERIC_RE.match(tokens[-1]) else None
        if value is None:
            break
        trailing.insert(0, value)
        tokens.pop()

    if not tokens or not trailing:
        return None

    item = Item(name=clean_text(tokens[0]))
    if len(tokens) > 1:
        item.spec = clean_text(" ".join(tokens[1:]))

    if len(trailing) == 3:
        item.qty, item.unit_price, item.amount = trailing
    elif len(trailing) == 2:
        item.unit_price, item.amount = trailing
    else:
        item.amount = trailing[0]
    return item


def extract_table(words: list[Word]) -> tuple[list[Item], dict[str, object], list[str]]:
    """단어 박스에서 품목과 문서 필드를 뽑는다.

    반환: (품목, 필드, 메모)
    """
    lines = group_lines(words)
    if not lines:
        return [], {}, ["단어를 하나도 얻지 못했습니다"]

    notes: list[str] = []
    fields: dict[str, object] = {}
    costs: dict[str, float] = {}

    header = detect_header(lines)
    if header is None:
        notes.append("표 머리글을 찾지 못해 줄 끝 숫자로 해석했습니다")

    items: list[Item] = []
    body = lines[header.line_index + 1 :] if header else lines

    for line in body:
        text = line.text.strip()
        if not text:
            continue

        # 비목/합계 집계 행은 품목이 아니라 문서 필드로 뺀다
        label, numbers = _split_label_and_numbers(line)
        if numbers:
            category = match_category(label)
            if category:
                costs.setdefault(category, numbers[-1])
                continue
            if is_total_label(label):
                fields.setdefault("합계", numbers[-1])
                continue

        item = item_from_columns(header.assign(line)) if header else item_from_text(text)
        if item is None or item.is_empty():
            continue
        item.category = match_category(item.name)
        items.append(item)

    for line in lines[: header.line_index] if header else lines[:3]:
        fields.update(_header_fields(line.text))

    if costs:
        fields["원가"] = costs
    return items, fields, notes


def _split_label_and_numbers(line: Line) -> tuple[str, list[float]]:
    """줄 앞쪽의 글자를 이름으로, 뒤쪽 숫자를 값으로 나눈다.

    OCR 은 한글을 '노 무 비' 처럼 띄어 읽는 일이 잦다. 첫 단어만 보면 비목을
    놓치므로, 숫자가 나오기 전까지의 단어를 모두 이어 붙여 이름으로 삼는다.
    """
    label_parts: list[str] = []
    numbers: list[float] = []
    for word in line.words:
        value = parse_number(word.text) if _NUMERIC_RE.match(word.text) else None
        if value is None and not numbers:
            label_parts.append(word.text)
        elif value is not None:
            numbers.append(value)
    return "".join(label_parts), numbers


def _header_fields(text: str) -> dict[str, str]:
    """'문서번호: Q-2024-001 업체: 가나산업' 같은 머리말에서 값을 뽑는다."""
    found: dict[str, str] = {}
    for labels, key in (
        (DOC_NO_LABELS, "문서번호"),
        (VENDOR_LABELS, "업체명"),
        (DATE_LABELS, "작성일"),
    ):
        for label in labels:
            match = re.search(rf"{label}\s*[:：]?\s*([^\s].*?)(?=\s{{2,}}|\s*[가-힣]+\s*[:：]|$)", text)
            if match:
                value = clean_text(match.group(1))
                if value:
                    found.setdefault(key, value)
                break
    return found
