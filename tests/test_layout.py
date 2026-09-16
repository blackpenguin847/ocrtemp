from ocrlab.layout import (
    Word,
    detect_header,
    extract_table,
    group_lines,
    item_from_text,
    match_category,
    match_column,
)


def row(y, cells):
    """(텍스트, x) 목록을 한 줄의 단어들로."""
    return [Word(text, x, y, x + 40, y + 12) for text, x in cells]


HEADER = [("품명", 100), ("규격", 200), ("단위", 300), ("수량", 380), ("단가", 460), ("금액", 560)]


def test_match_column_and_category():
    assert match_column("금액(원)") == "amount"
    assert match_column("단 가") == "unit_price"
    assert match_column("없는열") == ""
    assert match_category("일반관리비") == "관리비"
    assert match_category("이윤") == "영업이익"
    assert match_category("제경비") == "경비"


def test_group_lines_clusters_by_vertical_overlap():
    words = row(100, HEADER) + row(140, [("강판", 100)]) + row(143, [("2", 380)])
    lines = group_lines(words)
    assert len(lines) == 2
    assert "강판" in lines[1].text and "2" in lines[1].text


def test_detect_header_finds_the_widest_match():
    lines = group_lines(row(50, [("견적서", 100)]) + row(100, HEADER))
    header = detect_header(lines)
    assert header is not None
    assert header.line_index == 1
    assert {name for name, _ in header.columns} == {
        "name", "spec", "unit", "qty", "unit_price", "amount"
    }


def test_extract_table_maps_columns():
    words = (
        row(100, HEADER)
        + row(140, [("강판", 100), ("SS400", 200), ("㎡", 300), ("2", 380), ("35,000", 460), ("70,000", 560)])
    )
    items, _, notes = extract_table(words)
    assert notes == []
    assert len(items) == 1
    item = items[0]
    assert (item.name, item.spec, item.unit) == ("강판", "SS400", "㎡")
    assert (item.qty, item.unit_price, item.amount) == (2, 35000, 70000)


def test_cost_rows_go_to_fields_not_items():
    words = (
        row(100, HEADER)
        + row(140, [("강판", 100), ("SS400", 200), ("㎡", 300), ("2", 380), ("35,000", 460), ("70,000", 560)])
        + row(200, [("재료비", 100), ("70,000", 560)])
        + row(240, [("합계", 100), ("90,000", 560)])
    )
    items, fields, _ = extract_table(words)
    assert [item.name for item in items] == ["강판"]
    assert fields["원가"] == {"재료비": 70000}
    assert fields["합계"] == 90000


def test_cost_row_survives_ocr_splitting_korean():
    """OCR 은 '노무비' 를 '노 무 비' 로 띄어 읽는다."""
    words = (
        row(100, HEADER)
        + row(200, [("노", 100), ("무", 130), ("비", 160), ("400,000", 560)])
    )
    items, fields, _ = extract_table(words)
    assert items == []
    assert fields["원가"] == {"노무비": 400000}


def test_heuristic_reads_rows_without_a_header():
    items, _, notes = extract_table(
        row(140, [("강판", 100), ("SS400", 200), ("2", 380), ("35,000", 460), ("70,000", 560)])
    )
    assert any("머리글" in note for note in notes)
    assert items[0].qty == 2
    assert items[0].amount == 70000


def test_item_from_text_uses_trailing_numbers():
    item = item_from_text("강판 SS400 3.2T 2 35,000 70,000")
    assert item.name == "강판"
    assert (item.qty, item.unit_price, item.amount) == (2, 35000, 70000)
    assert item_from_text("설명만 있는 줄") is None


def test_header_fields_are_picked_up():
    words = (
        row(40, [("문서번호:", 100), ("Q-2024-001", 180)])
        + row(100, HEADER)
    )
    _, fields, _ = extract_table(words)
    assert fields.get("문서번호") == "Q-2024-001"


def test_empty_input_is_reported():
    items, fields, notes = extract_table([])
    assert items == [] and fields == {}
    assert notes and "단어" in notes[0]
