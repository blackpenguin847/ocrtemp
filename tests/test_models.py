from estimate_ocr.models import (
    AMOUNT_KEYS,
    QTY_KEYS,
    UNIT_PRICE_KEYS,
    Document,
    Item,
    parse_number,
    pick,
)


def test_parse_number_handles_korean_currency():
    assert parse_number("1,200") == 1200
    assert parse_number("₩ 3,500원") == 3500
    assert parse_number("(1,000)") == -1000
    assert parse_number("12.5") == 12.5
    assert parse_number(None) is None
    assert parse_number("-") is None


def test_pick_matches_aliases_and_partial_names():
    assert pick({"단 가": "1,000"}, UNIT_PRICE_KEYS) == "1,000"
    assert pick({"UnitPrice": 900}, UNIT_PRICE_KEYS) == 900
    assert pick({"금액(원)": "3,000"}, AMOUNT_KEYS) == "3,000"
    assert pick({"수량": ""}, QTY_KEYS) is None


def test_document_from_raw_maps_mixed_column_names():
    doc = Document.from_raw(
        {
            "견적번호": "Q-2024-001",
            "소계": "5,000",
            "items": [
                {"품명": "레미콘", "규격": "25-24-150", "단위": "㎥", "수량": "2", "단가": "1,500", "금액": "3,000"},
                {"name": "펌프카", "qty": 1, "unit_price": 2000, "amount": 2000},
                {"품명": "", "수량": "", "금액": ""},  # 빈 행은 버려진다
            ],
        },
        source="a.pdf",
        page=1,
    )
    assert doc.doc_no == "Q-2024-001"
    assert doc.subtotal == 5000
    assert len(doc.items) == 2
    assert doc.items[0].unit == "㎥"
    assert doc.items[1].name == "펌프카"
    assert doc.items_total() == 5000
    assert doc.validate() == []


def test_validate_flags_amount_mismatch_and_empty_name():
    doc = Document(
        source="a.pdf",
        page=1,
        items=[
            Item(name="레미콘", qty=2, unit_price=1500, amount=9999),
            Item(name="", qty=1, unit_price=100, amount=100),
        ],
    )
    codes = {issue.code for issue in doc.validate()}
    assert "amount_mismatch" in codes
    assert "empty_name" in codes
    assert not doc.is_ok()


def test_validate_flags_subtotal_mismatch():
    doc = Document(items=[Item(name="A", qty=1, unit_price=100, amount=100)], subtotal=500)
    assert any(issue.code == "subtotal_mismatch" for issue in doc.validate())


def test_validate_flags_empty_and_failed_documents():
    assert any(issue.code == "no_items" for issue in Document().validate())
    assert any(issue.code == "parse_error" for issue in Document(error="JSON 파싱 실패").validate())


def test_rounding_within_tolerance_is_not_flagged():
    doc = Document(items=[Item(name="A", qty=3, unit_price=333.33, amount=1000)])
    assert doc.validate() == []
