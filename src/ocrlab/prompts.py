"""LLM 엔진이 쓰는 프롬프트. 엔진끼리 비교하려면 스키마가 같아야 한다."""

from __future__ import annotations

CATEGORIES = ("재료비", "노무비", "경비", "관리비", "포장비", "영업이익")

SYSTEM = (
    "당신은 한국어 견적 원가 산출서를 판독하는 OCR 전문가입니다. "
    "실제로 적힌 내용만 옮기고, 보이지 않는 값은 추측하지 마십시오. "
    "숫자는 자릿수를 특히 주의해서 읽으십시오. "
    "요청받은 JSON 외의 설명이나 코드펜스는 출력하지 마십시오."
)

_COST_LINES = "\n".join(f'    "{name}": 숫자만 또는 null,' for name in CATEGORIES)

SCHEMA = f"""{{
  "문서번호": "", "업체명": "", "작성일": "",
  "원가": {{
{_COST_LINES}
    "합계": 숫자만 또는 null
  }},
  "품목": [
    {{
      "구분": "{' / '.join(CATEGORIES)} 중 하나 또는 \\"\\"",
      "품명": "", "규격": "", "단위": "",
      "수량": 숫자만 또는 null,
      "단가": 숫자만 또는 null,
      "금액": 숫자만 또는 null,
      "비고": ""
    }}
  ]
}}"""

RULES = """규칙:
1. 표의 행 순서를 그대로 유지하십시오.
2. 숫자에서 쉼표·원·₩ 는 빼고 숫자만 적으십시오. ("1,200원" → 1200)
3. 비어 있거나 판독 불가면 문자열은 "", 숫자는 null 로 두십시오. 추측 금지.
4. 비목 집계 행(재료비·노무비·합계 등)은 "품목" 이 아니라 "원가" 에 넣으십시오.
5. JSON 객체 하나만 출력하십시오."""

IMAGE_PROMPT = f"""이 이미지는 견적 원가 산출서 스캔본입니다.
표의 모든 행과 하단 집계를 읽어 아래 JSON 으로만 출력하십시오.

{SCHEMA}

{RULES}
"""

# 주의: 프롬프트에는 JSON 스키마의 중괄호가 그대로 들어 있다.
# str.format() 을 쓰면 그 중괄호를 치환 필드로 해석해 터지므로, 원문은 결합으로 붙인다.
TEXT_PROMPT_HEAD = f"""아래는 견적 원가 산출서에서 추출한 원문 텍스트입니다.
이 텍스트만 사용해 JSON 으로 구조화하십시오. 텍스트에 없는 값은 만들지 마십시오.

{SCHEMA}

{RULES}
"""

OCR_NOTE = """
이 원문은 숫자를 비교적 정확히 읽지만 글자를 틀리거나 칸이 밀릴 수 있습니다.
**판단 기준은 이미지이며**, 숫자 자릿수가 헷갈릴 때만 원문을 참고하십시오.
"""


def _wrap(text: str, title: str) -> str:
    return f"\n--- {title} 시작 ---\n{text.strip()}\n--- {title} 끝 ---\n"


def text_prompt(text: str) -> str:
    """원문 텍스트만으로 구조화시키는 프롬프트."""
    return TEXT_PROMPT_HEAD + _wrap(text, "원문")


def image_with_text_prompt(text: str) -> str:
    """이미지와 OCR 원문을 함께 주는 프롬프트."""
    return IMAGE_PROMPT + _wrap(text, "참고 원문") + OCR_NOTE
