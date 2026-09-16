"""프롬프트. 스키마를 그대로 품고 있어 문자열 포매팅과 충돌하기 쉽다."""

from ocrlab import prompts
from ocrlab.layout import CATEGORY_ALIASES
from ocrlab.ollama import loads_lenient

SAMPLE = "강판 SS400 2 35,000 70,000"


def test_text_prompt_embeds_the_source_text():
    prompt = prompts.text_prompt(SAMPLE)
    assert SAMPLE in prompt
    assert "원문 시작" in prompt


def test_image_with_text_prompt_marks_the_text_as_reference_only():
    prompt = prompts.image_with_text_prompt(SAMPLE)
    assert SAMPLE in prompt
    assert "참고 원문" in prompt
    assert "판단 기준은 이미지" in prompt


def test_text_prompt_does_not_claim_to_have_an_image():
    """이미지를 보내지 않는 엔진이 '이 이미지는' 으로 시작하면 안 된다."""
    assert "이미지" not in prompts.text_prompt(SAMPLE).split("---")[0]


def test_prompts_survive_braces_in_the_schema():
    """회귀: 스키마의 중괄호를 str.format 이 치환 필드로 읽어 KeyError 가 났다."""
    for text in (SAMPLE, "{이상한} 중괄호", "{}"):
        assert prompts.text_prompt(text)
        assert prompts.image_with_text_prompt(text)


def test_schema_is_valid_json_shaped():
    """스키마 자체가 JSON 모양이어야 모델이 따라 쓸 수 있다."""
    skeleton = prompts.SCHEMA
    for category in prompts.CATEGORIES:
        assert f'"{category}"' in skeleton
    assert '"품목"' in skeleton and '"원가"' in skeleton


def test_prompt_categories_match_the_layout_rules():
    """프롬프트가 요구하는 비목과 규칙 엔진이 아는 비목이 어긋나면 비교가 깨진다."""
    assert set(prompts.CATEGORIES) == set(CATEGORY_ALIASES)


def test_every_prompt_demands_a_single_json_object():
    for prompt in (prompts.IMAGE_PROMPT, prompts.text_prompt(SAMPLE),
                   prompts.image_with_text_prompt(SAMPLE)):
        assert "JSON 객체 하나만" in prompt


def test_lenient_parser_handles_model_quirks():
    assert loads_lenient('```json\n{"품목": []}\n```') == {"품목": []}
    assert loads_lenient('설명: {"품목": [{"금액": 1,200,},]}')["품목"][0]["금액"] == 1200
    assert loads_lenient('{"a": None, "b": True}') == {"a": None, "b": True}
    assert loads_lenient('{"품명": "브라켓 {특}"}') == {"품명": "브라켓 {특}"}
