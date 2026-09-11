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


# --------------------------------------------------------------------------
# 비목: 재료비 / 노무비 / 관리비 / 포장비 / 영업이익
# --------------------------------------------------------------------------
from estimate_ocr.models import (  # noqa: E402
    COST_CATEGORIES,
    CostSummary,
    is_cost_total_label,
    normalize_category,
)


def test_normalize_category_maps_common_aliases():
    assert normalize_category("재료비") == "재료비"
    assert normalize_category("자재비") == "재료비"
    assert normalize_category("직접노무비") == "노무비"
    assert normalize_category("인건비") == "노무비"
    assert normalize_category("일반관리비") == "관리비"
    assert normalize_category("경비") == "관리비"
    assert normalize_category("포장 및 운반비") == "포장비"
    assert normalize_category("이윤") == "영업이익"
    assert normalize_category("Labor") == "노무비"
    assert normalize_category("operating_profit") == "영업이익"
    assert normalize_category("운반비") == ""  # 5개 비목에 없는 값은 매핑하지 않는다
    assert normalize_category("") == ""


def test_is_cost_total_label():
    assert is_cost_total_label("합계")
    assert is_cost_total_label("총원가")
    assert not is_cost_total_label("재료비")


def test_cost_summary_from_mapping_and_list():
    mapping = CostSummary.from_raw(
        {"재료비": "1,000", "인건비": 400, "일반관리비": 70, "포장비": 30, "이윤": 150, "합계": "1,650"}
    )
    assert mapping.material == 1000
    assert mapping.labor == 400
    assert mapping.overhead == 70
    assert mapping.packaging == 30
    assert mapping.profit == 150
    assert mapping.total == 1650
    assert mapping.parts_sum() == 1650
    assert mapping.missing() == []

    listed = CostSummary.from_raw(
        [{"비목": "재료비", "금액": 1000}, {"비목": "영업이익", "금액": 150}]
    )
    assert listed.material == 1000
    assert listed.profit == 150
    assert listed.missing() == ["노무비", "관리비", "포장비"]


def test_cost_summary_keeps_unknown_categories_separately():
    summary = CostSummary.from_raw({"재료비": 100, "부가세": 10})
    assert summary.extras == {"부가세": 10}
    assert summary.parts_sum() == 100


def test_document_parses_cost_breakdown_and_item_categories():
    doc = Document.from_raw(
        {
            "원가": {"재료비": 3000, "노무비": 2000, "관리비": 500, "포장비": 300, "영업이익": 700, "합계": 6500},
            "품목": [
                {"구분": "재료비", "품명": "레미콘", "수량": 2, "단가": 1500, "금액": 3000},
                {"구분": "노무비", "품명": "인부", "수량": 1, "단가": 2000, "금액": 2000},
            ],
        }
    )
    assert doc.costs.parts_sum() == 6500
    assert doc.cost_total() == 6500
    assert doc.category_totals() == {"재료비": 3000, "노무비": 2000}
    assert doc.validate() == []


def test_cost_total_mismatch_is_flagged():
    doc = Document.from_raw(
        {"원가": {"재료비": 1000, "노무비": 500, "관리비": 100, "포장비": 50, "영업이익": 150, "합계": 2000}}
    )
    assert any(issue.code == "cost_total_mismatch" for issue in doc.validate())


def test_category_totals_mismatch_is_flagged():
    doc = Document.from_raw(
        {
            "원가": {"재료비": 9999, "노무비": 2000, "관리비": 500, "포장비": 300, "영업이익": 700},
            "품목": [{"구분": "재료비", "품명": "레미콘", "수량": 2, "단가": 1500, "금액": 3000}],
        }
    )
    codes = {issue.code for issue in doc.validate()}
    assert "category_mismatch" in codes


def test_missing_category_is_flagged_when_partially_present():
    doc = Document.from_raw({"원가": {"재료비": 1000, "노무비": 500}})
    issue = next(issue for issue in doc.validate() if issue.code == "missing_category")
    assert "포장비" in issue.message and "영업이익" in issue.message


def test_unknown_item_category_is_reported_once_per_document():
    doc = Document.from_raw(
        {
            "품목": [
                {"구분": "직접비", "품명": "A", "금액": 100},
                {"구분": "직접비", "품명": "B", "금액": 100},
            ]
        }
    )
    unknown = [issue for issue in doc.validate() if issue.code == "unknown_category"]
    assert len(unknown) == 1
    assert "직접비" in unknown[0].message


def test_category_rows_are_absorbed_but_detail_rows_survive():
    doc = Document.from_raw(
        {
            "품목": [
                {"품명": "재료비", "금액": 3000},                                   # 집계 행
                {"품명": "레미콘", "규격": "25-24", "수량": 2, "단가": 1500, "금액": 3000},  # 세부 행
            ]
        }
    )
    assert [item.name for item in doc.items] == ["레미콘"]
    assert doc.costs.material == 3000
    assert doc.items[0].category == ""


def test_subtotal_check_only_runs_without_cost_breakdown():
    with_costs = Document.from_raw(
        {
            "소계": 6500,
            "원가": {"재료비": 3000, "노무비": 2000, "관리비": 500, "포장비": 300, "영업이익": 700},
            "품목": [{"구분": "재료비", "품명": "레미콘", "금액": 3000}],
        }
    )
    # 품목 합계(3,000)와 총원가(6,500)는 원래 다르므로 소계 경고를 내면 안 된다
    assert not any(issue.code == "subtotal_mismatch" for issue in with_costs.validate())

    without_costs = Document.from_raw({"소계": 6500, "품목": [{"품명": "레미콘", "금액": 3000}]})
    assert any(issue.code == "subtotal_mismatch" for issue in without_costs.validate())


def test_cost_categories_are_the_five_requested():
    assert COST_CATEGORIES == ("재료비", "노무비", "관리비", "포장비", "영업이익")
