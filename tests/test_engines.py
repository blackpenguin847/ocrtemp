"""엔진별 동작. LLM 엔진은 가짜 클라이언트로, Tesseract 는 설치돼 있을 때만."""

import json

import pytest

from conftest import GRAND_TOTAL, HAS_TESSERACT, ITEMS_TOTAL, ROWS
from ocrlab.engines import ENGINE_NAMES, ENGINES, Options, build, get
from ocrlab.engines.llm import TesseractLLMEngine, VisionEngine, VisionOcrEngine
from ocrlab.engines.pdftext import PdfTextEngine
from ocrlab.ollama import OllamaError

ANSWER = {
    "문서번호": "Q-2024-001",
    "업체명": "가나산업",
    "원가": {"재료비": ITEMS_TOTAL, "노무비": 400000, "합계": GRAND_TOTAL},
    "품목": [
        {"품명": name, "규격": spec, "단위": unit, "수량": qty, "단가": price,
         "금액": qty * price}
        for name, spec, unit, qty, price in ROWS
    ],
}


class FakeClient:
    """OllamaClient 자리에 끼우는 스텁. 무엇을 받았는지 기록한다."""

    def __init__(self, response=None, error=None):
        self.response = json.dumps(response or ANSWER, ensure_ascii=False)
        self.error = error
        self.calls = []
        self.model = "fake"

    def generate(self, prompt, *, images=None, system=None, json_mode=False):
        self.calls.append({"prompt": prompt, "images": images})
        if self.error:
            raise self.error
        return self.response

    def list_models(self):
        return ["fake"]

    def has_model(self):
        return True


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("ocrlab.engines.llm._LLMEngine.client", lambda self: client)
    return client


# --------------------------------------------------------------------------
# 등록소
# --------------------------------------------------------------------------
def test_every_engine_has_a_name_and_description():
    for engine in ENGINES:
        assert engine.name and engine.description
        assert engine.name == engine.name.lower()
    assert len(set(ENGINE_NAMES)) == len(ENGINE_NAMES)


def test_get_rejects_unknown_engine():
    with pytest.raises(KeyError, match="알 수 없는 엔진"):
        get("없는엔진")


def test_run_turns_failure_into_a_result(monkeypatch, digital_pdf):
    engine = PdfTextEngine(Options())
    monkeypatch.setattr(engine, "extract", lambda pdf: 1 / 0)

    result = engine.run(digital_pdf)
    assert not result.ok
    assert "ZeroDivisionError" in result.error
    assert result.elapsed >= 0          # 실패해도 다른 엔진 비교를 막지 않는다


# --------------------------------------------------------------------------
# pdftext
# --------------------------------------------------------------------------
def test_pdftext_reads_the_text_layer(digital_pdf):
    result = PdfTextEngine(Options()).run(digital_pdf)
    assert result.ok
    assert [item.name for item in result.items] == [name for name, *_ in ROWS]
    assert result.total_amount() == ITEMS_TOTAL
    assert result.pages[0].fields["원가"]["재료비"] == ITEMS_TOTAL
    assert result.arithmetic_rate() == 1.0


def test_pdftext_finds_nothing_in_a_scan_and_says_so(scan_pdf):
    result = PdfTextEngine(Options()).run(scan_pdf)
    assert result.ok                     # 실패가 아니라 "0건" 이 정답이다
    assert result.items == []
    assert any("스캔" in note for note in result.pages[0].notes)


def test_pages_option_limits_work(digital_pdf):
    result = PdfTextEngine(Options(pages=[1])).run(digital_pdf)
    assert [page.page for page in result.pages] == [1]


# --------------------------------------------------------------------------
# tesseract (설치돼 있을 때만)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract 미설치")
def test_tesseract_reads_a_scanned_pdf(scan_pdf):
    result = build("tesseract", Options(dpi=300)).run(scan_pdf)
    assert result.ok, result.error

    names = [item.name for item in result.items]
    assert names == [name for name, *_ in ROWS]
    assert result.total_amount() == ITEMS_TOTAL
    assert result.pages[0].confidence > 50
    # 비목 집계는 품목이 아니라 필드로 간다
    assert result.pages[0].fields["원가"]["재료비"] == ITEMS_TOTAL


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract 미설치")
def test_tesseract_check_reports_language(scan_pdf):
    status = build("tesseract", Options()).check()
    assert status.ok
    assert "kor" in status.detail


def test_tesseract_check_explains_missing_language():
    status = build("tesseract", Options(lang="존재하지않는언어")).check()
    assert not status.ok
    assert "언어 데이터" in status.hint or "tesseract" in status.hint


# --------------------------------------------------------------------------
# LLM 엔진: 무엇을 보내는지가 엔진의 정체다
# --------------------------------------------------------------------------
def test_vision_sends_the_image_only(fake_client, scan_pdf):
    result = VisionEngine(Options(pages=[1])).run(scan_pdf)
    assert result.ok, result.error

    call = fake_client.calls[0]
    assert call["images"]                          # 이미지를 보냈다
    assert "참고 원문" not in call["prompt"]        # OCR 원문은 안 보냈다
    assert result.total_amount() == ITEMS_TOTAL


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract 미설치")
def test_tesseract_llm_sends_text_without_the_image(fake_client, scan_pdf):
    result = TesseractLLMEngine(Options(pages=[1], dpi=300)).run(scan_pdf)
    assert result.ok, result.error

    call = fake_client.calls[0]
    assert not call["images"]                      # 이미지를 보내지 않는다
    assert "강판" in call["prompt"]                 # OCR 원문이 실렸다
    assert result.pages[0].confidence > 50


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract 미설치")
def test_vision_ocr_sends_both(fake_client, scan_pdf):
    result = VisionOcrEngine(Options(pages=[1], dpi=300)).run(scan_pdf)
    assert result.ok, result.error

    call = fake_client.calls[0]
    assert call["images"]
    assert "참고 원문" in call["prompt"]
    assert "강판" in call["prompt"]


def test_llm_engine_failure_is_captured(monkeypatch, scan_pdf):
    client = FakeClient(error=OllamaError("연결 실패"))
    monkeypatch.setattr("ocrlab.engines.llm._LLMEngine.client", lambda self: client)

    result = VisionEngine(Options(pages=[1])).run(scan_pdf)
    assert not result.ok
    assert "연결 실패" in result.error


def test_llm_engine_reports_unreachable_server():
    status = VisionEngine(Options(host="http://127.0.0.1:1")).check()
    assert not status.ok
    assert "Ollama" in status.hint


def test_engine_meta_records_run_conditions(fake_client, scan_pdf):
    result = VisionEngine(Options(pages=[1], dpi=123, model="m")).run(scan_pdf)
    assert result.meta["dpi"] == 123
    assert result.meta["model"] == "m"
