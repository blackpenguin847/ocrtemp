"""Tesseract OCR 보조 경로.

로컬 vision 모델은 문맥과 표 구조를 잘 잡는 대신 숫자를 자주 틀리고, Tesseract 는
문맥은 모르지만 인쇄체 숫자를 결정적으로 읽는다. 둘을 겹쳐 쓰면

- vision 모델이 아예 못 읽는 경우(모델이 이미지를 못 받거나 OOM)에도 원문을 확보하고
- vision 모델이 읽은 금액을 OCR 원문과 대조해 오인식을 검수 시트로 끌어낼 수 있다.

Tesseract 는 선택 사항이다. 설치돼 있지 않으면 이 모듈은 조용히 비활성화된다.

설치:
    pip install "estimate-ocr[tesseract]"
    # 그리고 시스템 패키지 (한국어 데이터 포함)
    sudo apt install tesseract-ocr tesseract-ocr-kor   # Debian/Ubuntu
    brew install tesseract tesseract-lang              # macOS
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from PIL import Image

log = logging.getLogger(__name__)

DEFAULT_LANG = "kor+eng"
#: 견적서처럼 표가 한 덩어리로 있는 문서는 psm 4(가변 크기 단일 컬럼)가 가장 잘 맞는다.
#: psm 6(균일 블록)은 표의 행을 통째로 날리는 경우가 있다.
DEFAULT_PSM = 4
#: 이 값보다 신뢰도가 낮으면 교차검증에 쓰지 않는다 (오히려 오경보만 늘어난다)
MIN_CROSSCHECK_CONFIDENCE = 60.0

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class TesseractError(RuntimeError):
    """Tesseract 를 쓸 수 없거나 실행이 실패한 경우."""


@dataclass
class TesseractResult:
    """페이지 한 장의 OCR 결과."""

    text: str = ""
    confidence: float = 0.0   # 단어 신뢰도 평균 (0~100)
    words: int = 0
    numbers: set[float] = field(default_factory=set)

    def is_usable(self) -> bool:
        return bool(self.text.strip()) and self.words > 0

    def is_confident(self, minimum: float = MIN_CROSSCHECK_CONFIDENCE) -> bool:
        return self.is_usable() and self.confidence >= minimum

    def has_number(self, value: float, *, tolerance: float = 0.51) -> bool:
        """OCR 원문에 이 숫자가 있는지. 원 단위 반올림 차이는 같은 값으로 본다."""
        return any(abs(value - candidate) <= tolerance for candidate in self.numbers)


def extract_numbers(text: str) -> set[float]:
    """'35,000원' → 35000.0. 교차검증용으로 본문의 숫자를 모두 긁는다."""
    found: set[float] = set()
    for token in _NUMBER_RE.findall(text):
        cleaned = token.replace(",", "").rstrip(".")
        if not cleaned:
            continue
        try:
            found.add(float(cleaned))
        except ValueError:
            continue
        # '1,400,000' 을 '1400000' 과 '1,400,000' 둘 다로 읽는 오인식에 대비해
        # 쉼표를 자릿수 구분이 아닌 것으로 본 해석도 함께 담는다.
        if "," in token:
            for part in token.split(","):
                if part.isdigit():
                    found.add(float(part))
    return found


class TesseractOCR:
    """pytesseract 얇은 감싸개. 설치돼 있지 않으면 `available()` 이 False."""

    def __init__(
        self,
        *,
        lang: str = DEFAULT_LANG,
        psm: int = DEFAULT_PSM,
        extra_config: str = "",
    ) -> None:
        self.lang = lang
        self.psm = psm
        self.extra_config = extra_config
        self._module: Any | None = None

    # -- 가용성 -----------------------------------------------------------
    def _pytesseract(self) -> Any:
        if self._module is None:
            try:
                import pytesseract
            except ImportError as exc:  # pragma: no cover - 설치 여부에 달림
                raise TesseractError(
                    "pytesseract 가 설치되어 있지 않습니다. "
                    '`pip install "estimate-ocr[tesseract]"` 를 실행하세요.'
                ) from exc
            self._module = pytesseract
        return self._module

    def available(self) -> bool:
        """pytesseract 와 tesseract 실행 파일이 모두 준비됐는지."""
        try:
            self.version()
        except TesseractError:
            return False
        return True

    def version(self) -> str:
        module = self._pytesseract()
        try:
            return str(module.get_tesseract_version())
        except Exception as exc:  # pytesseract.TesseractNotFoundError 등
            raise TesseractError(
                "tesseract 실행 파일을 찾을 수 없습니다. "
                "apt install tesseract-ocr tesseract-ocr-kor (또는 brew install tesseract tesseract-lang) "
                "로 설치하세요."
            ) from exc

    def languages(self) -> list[str]:
        module = self._pytesseract()
        try:
            return list(module.get_languages(config=""))
        except Exception as exc:
            raise TesseractError(f"설치된 언어 목록을 읽지 못했습니다: {exc}") from exc

    def missing_languages(self) -> list[str]:
        """`lang` 에 지정했지만 설치돼 있지 않은 언어."""
        try:
            installed = set(self.languages())
        except TesseractError:
            return []
        return [name for name in self.lang.split("+") if name and name not in installed]

    # -- 실행 -------------------------------------------------------------
    @property
    def config(self) -> str:
        config = f"--psm {self.psm}"
        return f"{config} {self.extra_config}".strip()

    def read(self, image: Image.Image) -> TesseractResult:
        """이미지 한 장을 읽어 줄 구조를 유지한 텍스트와 신뢰도를 돌려준다."""
        module = self._pytesseract()
        try:
            data = module.image_to_data(
                image,
                lang=self.lang,
                config=self.config,
                output_type=module.Output.DICT,
            )
        except Exception as exc:
            raise TesseractError(f"Tesseract 실행에 실패했습니다: {exc}") from exc

        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []

        for index, word in enumerate(data.get("text", [])):
            text = (word or "").strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][index])
            except (KeyError, IndexError, TypeError, ValueError):
                confidence = -1.0
            if confidence >= 0:
                confidences.append(confidence)

            key = (
                data.get("block_num", [0] * (index + 1))[index],
                data.get("par_num", [0] * (index + 1))[index],
                data.get("line_num", [0] * (index + 1))[index],
            )
            lines.setdefault(key, []).append(text)

        text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
        result = TesseractResult(
            text=text,
            confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            words=len(confidences) or sum(len(words) for words in lines.values()),
            numbers=extract_numbers(text),
        )
        log.debug(
            "Tesseract: 단어 %d개, 신뢰도 %.1f, 숫자 %d개",
            result.words, result.confidence, len(result.numbers),
        )
        return result
