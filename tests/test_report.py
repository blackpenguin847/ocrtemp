import json

from ocrlab.report import format_table, summary_payload, write_results
from ocrlab.schema import EngineResult, Item, PageResult


def results():
    good = EngineResult(
        engine="tesseract",
        pdf="a.pdf",
        pages=[PageResult(1, [Item(name="강판", qty=2, unit_price=35000, amount=70000)])],
        elapsed=0.9,
        meta={"dpi": 300},
    )
    empty = EngineResult(engine="pdftext", pdf="a.pdf", pages=[PageResult(1, [])], elapsed=0.02)
    failed = EngineResult(engine="vision", pdf="a.pdf", ok=False, error="연결 실패", elapsed=0.1)
    return [empty, good, failed]


def test_table_lists_every_engine_and_failures():
    table = format_table(results())
    for name in ("pdftext", "tesseract", "vision"):
        assert name in table
    assert "70,000" in table          # 정수는 소수점 없이
    assert "70,000.00" not in table
    assert "실패" in table
    assert "vision 실패: 연결 실패" in table


def test_table_columns_line_up_with_korean_text():
    lines = format_table(results()).splitlines()
    divider = lines[1]
    # 구분선과 머리글의 표시 폭이 같아야 열이 어긋나지 않는다
    import unicodedata

    def width(text):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)

    assert width(lines[0]) == width(divider)


def test_write_results_makes_one_file_per_engine(tmp_path):
    written = write_results(results(), tmp_path / "out", pdf="a.pdf")
    names = {path.name for path in written}
    assert names == {"pdftext.json", "tesseract.json", "vision.json", "summary.json"}

    payload = json.loads((tmp_path / "out" / "tesseract.json").read_text(encoding="utf-8"))
    assert payload["엔진"] == "tesseract"
    assert payload["지표"]["품목수"] == 1
    assert payload["페이지"][0]["품목"][0]["품명"] == "강판"
    assert payload["실행조건"]["dpi"] == 300


def test_summary_has_every_engine():
    payload = summary_payload(results(), "a.pdf")
    assert set(payload["엔진"]) == {"pdftext", "tesseract", "vision"}
    assert payload["엔진"]["vision"]["성공"] is False
