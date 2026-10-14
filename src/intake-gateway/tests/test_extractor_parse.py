import pytest

from app.extractor import parse_json_lenient


def test_fenced_with_trailing_text():
    assert parse_json_lenient('```json\n{"a": [1, 2]} trailing') == {"a": [1, 2]}


def test_truncated_string_and_object():
    assert parse_json_lenient('{"fields":{"a":"hel') == {"fields": {"a": "hel"}}


def test_truncated_array():
    assert parse_json_lenient('{"a":[1,2,') == {"a": [1, 2]}


def test_no_json():
    with pytest.raises(ValueError):
        parse_json_lenient("sorry, no data")
