"""Tesseract 보조 경로. Tesseract 없이도 대부분 검증되도록 가짜 OCR 을 쓴다."""

import json
import shutil

import pytest

from estimate_ocr import Extractor
from estimate_ocr.models import Document, Item
from estimate_ocr.tesseract_ocr import TesseractError, TesseractOCR, TesseractResult, extract_numbers

from test_pipeline import GOOD_JSON, FakeClient, sample_pdf  # noqa: F401

HAS_TESSERACT = shutil.which("tesseract") is not None


class FakeOCR:
    """TesseractOCR 와 같은 모양의 스텁."""

    def __init__(self, text="", confidence=90.0, error=None, missing=()):
        self.text = text
        self.confidence = confidence
        self.error = error
        self.missing = list(missing)
        self.reads = 0

    def version(self):
        if self.error:
            raise TesseractError(self.error)
        return "5.3.4"

    def missing_languages(self):
        return self.missing

    def read(self, image):
        self.reads += 1
        if self.error:
            raise TesseractError(self.error)
        return TesseractResult(
            text=self.text,
            confidence=self.confidence,
            words=len(self.text.split()),
            numbers=extract_numbers(self.text),
        )


OCR_TEXT = "강판 00 SS400 3.2T 2 35,000 70,000\n강판 01 SS400 3.2T 2 35,000 70,000"


# --------------------------------------------------------------------------
# 숫자 추출과 대조
# --------------------------------------------------------------------------
def test_extract_numbers_handles_thousand_separators():
    numbers = extract_numbers("단가 35,000원 금액 1,400,000")
    assert 35000 in numbers
    assert 1400000 in numbers


def test_extract_numbers_also_keeps_split_interpretation():
    """'1,400,000' 을 자릿수 구분으로 못 읽은 OCR 결과와도 대조되도록."""
    numbers = extract_numbers("합계 1,400,000")
    assert 1400000 in numbers
    assert 400 in numbers


def test_result_has_number_absorbs_rounding():
    result = TesseractResult(text="70,000", numbers={70000.0}, words=1, confidence=90)
    assert result.has_number(70000)
    assert result.has_number(70000.4)
    assert not result.has_number(75000)


def test_result_confidence_gate():
    weak = TesseractResult(text="x", words=1, confidence=20.0)
    assert weak.is_usable()
    assert not weak.is_confident()


# --------------------------------------------------------------------------
# 교차검증
# --------------------------------------------------------------------------
def _doc_with_ocr(items, numbers):
    return Document(source="a.pdf", page=1, items=items, ocr_numbers=set(numbers))


def test_crosscheck_flags_only_the_row_that_disagrees():
    doc = _doc_with_ocr(
        [
            Item(name="강판 00", qty=2, unit_price=35000, amount=70000),
            Item(name="강판 01", qty=2, unit_price=35000, amount=75000),  # OCR 에 없음
        ],
        {35000, 70000},
    )
    issues = [issue for issue in doc.validate() if issue.code == "ocr_mismatch"]
    assert len(issues) == 1
    assert issues[0].row == 2


def test_crosscheck_stays_quiet_when_everything_matches():
    doc = _doc_with_ocr([Item(name="A", qty=1, unit_price=100, amount=100)], {100})
    assert not any(issue.code.startswith("ocr_") for issue in doc.validate())


def test_crosscheck_falls_back_to_one_warning_when_ocr_read_nothing():
    """OCR 이 표를 통째로 놓쳤을 때 모든 행을 노랗게 칠하지 않는다."""
    items = [Item(name=f"A{i}", qty=1, unit_price=100 + i, amount=200 + i) for i in range(5)]
    doc = _doc_with_ocr(items, {99999})
    codes = [issue.code for issue in doc.validate()]
    assert codes.count("ocr_unverified") == 1
    assert "ocr_mismatch" not in codes


def test_crosscheck_is_skipped_without_ocr_numbers():
    doc = Document(items=[Item(name="A", amount=123)])
    assert not any(issue.code.startswith("ocr_") for issue in doc.validate())


# --------------------------------------------------------------------------
# Extractor 통합
# --------------------------------------------------------------------------
def test_ocr_only_mode_never_sends_images(sample_pdf):
    client = FakeClient([GOOD_JSON])
    ocr = FakeOCR(OCR_TEXT)
    docs = Extractor(client, ocr=ocr, ocr_mode="only").extract_pdf(sample_pdf, pages=[1])

    assert ocr.reads == 1
    assert client.calls == ["generate_json"]      # 이미지 경로를 타지 않았다
    assert docs[0].ocr_text == OCR_TEXT
    assert docs[0].raw_text == OCR_TEXT


def test_ocr_assist_mode_puts_ocr_text_into_the_prompt(sample_pdf):
    captured = {}

    class Capturing(FakeClient):
        def generate_json(self, prompt, *, images=None, system=None):
            captured["prompt"] = prompt
            captured["images"] = images
            return super().generate_json(prompt, images=images, system=system)

    client = Capturing([GOOD_JSON])
    docs = Extractor(client, ocr=FakeOCR(OCR_TEXT), ocr_mode="assist").extract_pdf(
        sample_pdf, pages=[1]
    )
    assert "강판 00" in captured["prompt"]     # OCR 원문이 프롬프트에 실렸다
    assert captured["images"]                  # 이미지도 함께 보냈다
    assert len(docs[0].items) == 2


def test_ocr_rescues_a_page_the_vision_model_failed(sample_pdf):
    """vision 3단계가 모두 실패해도 OCR 원문으로 구조화한다."""
    client = FakeClient(["JSON 아님", "JSON 아님", "", GOOD_JSON])
    docs = Extractor(client, ocr=FakeOCR(OCR_TEXT), ocr_mode="auto").extract_pdf(
        sample_pdf, pages=[1]
    )
    assert docs[0].attempts == 4
    assert len(docs[0].items) == 2
    assert not docs[0].error


def test_auto_mode_runs_ocr_even_when_vision_succeeds(sample_pdf):
    """성공한 페이지에도 OCR 을 돌려야 교차검증이 된다."""
    ocr = FakeOCR(OCR_TEXT)
    docs = Extractor(FakeClient([GOOD_JSON]), ocr=ocr, ocr_mode="auto").extract_pdf(
        sample_pdf, pages=[1]
    )
    assert ocr.reads == 1
    assert docs[0].ocr_confidence == 90.0


def test_low_confidence_ocr_is_not_used_for_crosscheck(sample_pdf):
    ocr = FakeOCR(OCR_TEXT, confidence=20.0)
    docs = Extractor(FakeClient([GOOD_JSON]), ocr=ocr, ocr_mode="auto").extract_pdf(
        sample_pdf, pages=[1]
    )
    assert docs[0].ocr_text == OCR_TEXT
    assert docs[0].ocr_numbers == set()         # 대조에는 쓰지 않는다
    assert not any(issue.code.startswith("ocr_") for issue in docs[0].validate())


def test_missing_tesseract_disables_auto_mode_but_fails_only_mode(sample_pdf):
    broken = FakeOCR(error="tesseract 실행 파일을 찾을 수 없습니다")

    extractor = Extractor(FakeClient([GOOD_JSON]), ocr=broken, ocr_mode="auto")
    assert not extractor.ocr_enabled            # 조용히 꺼진다
    assert extractor.extract_pdf(sample_pdf, pages=[1])[0].items

    with pytest.raises(TesseractError):
        Extractor(FakeClient([]), ocr=broken, ocr_mode="only")


def test_unknown_ocr_mode_is_rejected():
    with pytest.raises(ValueError, match="OCR 모드"):
        Extractor(FakeClient([]), ocr_mode="nope")


def test_ocr_image_keeps_full_resolution(sample_pdf):
    from estimate_ocr.pdf_render import render_pdf

    page = next(render_pdf(sample_pdf, dpi=300, max_edge=600, pages=[1]))
    assert max(page.image.size) <= 600
    assert max(page.ocr_image.size) > 600       # OCR 은 축소 전 이미지를 받는다


# --------------------------------------------------------------------------
# 실제 Tesseract (설치돼 있을 때만)
# --------------------------------------------------------------------------
@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract 가 설치돼 있지 않음")
def test_real_tesseract_reads_a_rendered_page(tmp_path):
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1600, 600), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    except OSError:  # pragma: no cover
        pytest.skip("사용할 수 있는 트루타입 폰트가 없음")
    draw.text((60, 80), "ITEM 01   2   35,000   70,000", font=font, fill="black")

    path = tmp_path / "scan.png"
    image.save(path)

    result = TesseractOCR(lang="eng").read(Image.open(path))
    assert result.is_usable()
    assert result.has_number(35000)
    assert result.has_number(70000)
