from ocrlab.schema import EngineResult, Item, PageResult, clean_text, parse_number


def test_parse_number_handles_korean_currency():
    assert parse_number("1,200") == 1200
    assert parse_number("₩ 3,500원") == 3500
    assert parse_number("(1,000)") == -1000
    assert parse_number(None) is None
    assert parse_number("-") is None


def test_clean_text_keeps_unit_symbols():
    # NFKC 를 쓰면 ㎥ 가 m3 로 바뀌어 원본 대조가 어긋난다
    assert clean_text(" 강판  판재 ") == "강판 판재"
    assert clean_text("㎥") == "㎥"


def test_item_arithmetic_check():
    assert Item(qty=2, unit_price=1500, amount=3000).arithmetic_ok() is True
    assert Item(qty=2, unit_price=1500, amount=9999).arithmetic_ok() is False
    assert Item(qty=2, amount=3000).arithmetic_ok() is None      # 판정 불가
    assert Item(qty=3, unit_price=333.33, amount=1000).arithmetic_ok() is True


def test_engine_result_metrics():
    result = EngineResult(
        engine="x",
        pdf="a.pdf",
        pages=[
            PageResult(1, [Item(name="A", qty=2, unit_price=100, amount=200)]),
            PageResult(2, [Item(name="B", qty=1, unit_price=50, amount=999)]),
        ],
        elapsed=1.5,
    )
    metrics = result.metrics()
    assert metrics["품목수"] == 2
    assert metrics["금액합계"] == 1199
    assert result.arithmetic_rate() == 0.5
    assert 0 < result.fill_rate() < 1


def test_metrics_are_none_without_items():
    empty = EngineResult(engine="x", pdf="a.pdf", pages=[PageResult(1, [])])
    assert empty.metrics()["금액합계"] is None
    assert empty.fill_rate() is None
    assert empty.arithmetic_rate() is None
