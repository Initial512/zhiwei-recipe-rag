import pytest
from rag_modules.structured_output import StructuredOutputParseError, parse_json_object


@pytest.mark.parametrize(
    ("content", "source"),
    [
        ('{"value": 1}', "raw_json"),
        ('```json\n{"value": 1}\n```', "markdown_fence"),
        ('分析如下： {"value": 1} 请继续。', "embedded_json"),
    ],
)
def test_parse_json_object_accepts_common_llm_response_shapes(content, source):
    result, parsed_source = parse_json_object(content)

    assert result == {"value": 1}
    assert parsed_source == source


@pytest.mark.parametrize("content", ["", "not json", "[1, 2, 3]"])
def test_parse_json_object_rejects_invalid_or_non_object_responses(content):
    with pytest.raises(StructuredOutputParseError) as error:
        parse_json_object(content)

    assert "response_length=" in str(error.value)
