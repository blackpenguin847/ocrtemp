"""프롬프트가 비목 정의와 어긋나지 않는지 확인한다."""

from estimate_ocr import prompts
from estimate_ocr.models import COST_CATEGORIES


def test_every_category_appears_in_prompts():
    for category in COST_CATEGORIES:
        assert category in prompts.USER_PROMPT
        assert category in prompts.FALLBACK_PROMPT
        assert category in prompts.JSON_SCHEMA_HINT
        assert category in prompts.RAW_TEXT_PROMPT
        assert category in prompts.text_to_json_prompt("원문")


def test_prompt_schema_is_generated_from_the_constant():
    assert prompts.COST_SCHEMA_INLINE == ", ".join(
        f'"{category}": null' for category in COST_CATEGORIES
    )
    # 프롬프트에 남은 옛 비목 표기가 없어야 한다
    assert "경비 → 관리비" not in prompts.USER_PROMPT


def test_user_prompt_keeps_summary_rows_out_of_the_item_table():
    assert '"품목" 배열에 넣지 말고' in prompts.USER_PROMPT


def test_text_to_json_prompt_embeds_the_source_text():
    assert "원문내용" in prompts.text_to_json_prompt("원문내용")
