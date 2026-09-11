import pytest

from estimate_ocr.ollama_client import JSONParseError, loads_lenient


def test_loads_plain_json():
    assert loads_lenient('{"a": 1}') == {"a": 1}


def test_loads_code_fenced_json():
    assert loads_lenient('```json\n{"a": 1}\n```') == {"a": 1}


def test_loads_json_with_surrounding_prose():
    assert loads_lenient('다음과 같습니다:\n{"품목": []}\n감사합니다.') == {"품목": []}


def test_loads_json_with_trailing_commas_and_thousand_separators():
    parsed = loads_lenient('{"품목": [{"수량": 2, "금액": 1,200,},],}')
    assert parsed["품목"][0]["금액"] == 1200


def test_loads_python_literals():
    assert loads_lenient('{"a": None, "b": True, "c": NaN}') == {"a": None, "b": True, "c": None}


def test_braces_inside_strings_do_not_break_span_detection():
    assert loads_lenient('{"품명": "브라켓 {특}"}') == {"품명": "브라켓 {특}"}


def test_empty_response_raises():
    with pytest.raises(JSONParseError):
        loads_lenient("   ")


def test_garbage_response_raises():
    with pytest.raises(JSONParseError):
        loads_lenient("이미지를 읽을 수 없습니다.")
