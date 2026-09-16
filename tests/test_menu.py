import pytest

from ocrlab.engines import ENGINES, Options
from ocrlab.menu import MAX_LABEL, SelectionError, choose_engines, parse_selection, render_menu


def test_parse_selection_accepts_numbers_names_and_all():
    assert [e.name for e in parse_selection("1,2", ENGINES)] == ["pdftext", "tesseract"]
    assert [e.name for e in parse_selection("vision", ENGINES)] == ["vision"]
    assert [e.name for e in parse_selection(" 2 , vision ", ENGINES)] == ["tesseract", "vision"]
    assert parse_selection("a", ENGINES) == list(ENGINES)
    assert parse_selection("all", ENGINES) == list(ENGINES)


def test_parse_selection_deduplicates():
    assert [e.name for e in parse_selection("1,1,pdftext", ENGINES)] == ["pdftext"]


def test_quit_returns_nothing():
    assert parse_selection("q", ENGINES) == []
    assert parse_selection("종료", ENGINES) == []


@pytest.mark.parametrize("text", ["", "   ", "0", "99", "없는엔진"])
def test_bad_selection_is_rejected(text):
    with pytest.raises(SelectionError):
        parse_selection(text, ENGINES)


def test_menu_lists_every_engine_with_status():
    menu, engines = render_menu(Options(host="http://127.0.0.1:1"))
    assert engines == list(ENGINES)
    for engine in ENGINES:
        assert engine.name in menu
        assert engine.description in menu
    assert "a=전체" in menu
    # 상태 문구가 한 줄을 넘기지 않는다
    for line in menu.splitlines():
        assert len(line) <= MAX_LABEL + 20


def test_choose_engines_retries_until_valid(capsys):
    answers = iter(["엉뚱한값", "2"])
    chosen = choose_engines(Options(), reader=lambda prompt: next(answers))
    assert [engine.name for engine in chosen] == ["tesseract"]
    assert "다시 입력" in capsys.readouterr().err


def test_choose_engines_handles_eof():
    def reader(prompt):
        raise EOFError

    assert choose_engines(Options(), reader=reader) == []
