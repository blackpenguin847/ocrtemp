import json

import pytest

from ocrlab.cli import build_parser, main


def test_pdftext_run_writes_json(tmp_path, digital_pdf, capsys):
    code = main([str(digital_pdf), "-e", "pdftext", "-o", str(tmp_path / "out")])
    assert code == 0

    payload = json.loads((tmp_path / "out" / "pdftext.json").read_text(encoding="utf-8"))
    assert payload["엔진"] == "pdftext"
    assert payload["지표"]["품목수"] == 3

    err = capsys.readouterr().err
    assert "텍스트 레이어 있음" in err
    assert "pdftext" in err


def test_scan_pdf_is_reported_as_having_no_text_layer(tmp_path, scan_pdf, capsys):
    main([str(scan_pdf), "-e", "pdftext", "-o", str(tmp_path / "out")])
    assert "텍스트 레이어 없음 (스캔 PDF)" in capsys.readouterr().err


def test_unavailable_engine_is_skipped_not_crashed(tmp_path, scan_pdf, capsys):
    code = main([
        str(scan_pdf), "-e", "pdftext,vision", "-o", str(tmp_path / "out"),
        "--host", "http://127.0.0.1:1",
    ])
    assert code == 0                     # 쓸 수 있는 엔진이 하나라도 있으면 성공

    payload = json.loads((tmp_path / "out" / "vision.json").read_text(encoding="utf-8"))
    assert payload["성공"] is False
    assert "사용할 수 없음" in payload["오류"]
    assert "건너뜀" in capsys.readouterr().err


def test_all_engines_failing_returns_nonzero(tmp_path, scan_pdf):
    code = main([
        str(scan_pdf), "-e", "vision", "-o", str(tmp_path / "out"),
        "--host", "http://127.0.0.1:1",
    ])
    assert code == 1


def test_missing_file_is_reported(tmp_path, capsys):
    assert main([str(tmp_path / "없음.pdf"), "-e", "pdftext"]) == 2
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_non_pdf_is_rejected(tmp_path, capsys):
    other = tmp_path / "a.txt"
    other.write_text("x")
    assert main([str(other), "-e", "pdftext"]) == 2
    assert "PDF 파일이 아닙니다" in capsys.readouterr().err


def test_bad_page_spec_is_reported(digital_pdf, capsys):
    assert main([str(digital_pdf), "-e", "pdftext", "--pages", "a-b"]) == 2
    assert "페이지" in capsys.readouterr().err


def test_unknown_engine_is_reported(digital_pdf, capsys):
    assert main([str(digital_pdf), "-e", "없는엔진"]) == 2
    assert "이해할 수 없는 선택" in capsys.readouterr().err


def test_non_interactive_without_engine_explains_itself(digital_pdf, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main([str(digital_pdf)]) == 2
    assert "-e" in capsys.readouterr().err


def test_parser_exposes_shared_options():
    args = build_parser().parse_args(["a.pdf", "--dpi", "300", "--psm", "6", "--lang", "eng"])
    assert (args.dpi, args.psm, args.lang) == (300, 6, "eng")
