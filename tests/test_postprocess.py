"""文本后处理测试。"""

from __future__ import annotations

from app.postprocess import clean_text, detect_language, join_texts, strip_tags


def test_strip_tags_removes_rich_tags():
    raw = "<|zh|><|NEUTRAL|><|Speech|><|withitn|>今天天气不错。"
    assert strip_tags(raw) == "今天天气不错。"


def test_clean_text_handles_english():
    raw = "<|en|><|HAPPY|><|Speech|>Hello world, this is a test."
    assert clean_text(raw) == "Hello world, this is a test."


def test_clean_text_removes_spaces_between_chinese_chars():
    assert clean_text("你好 世界 ，今天 天气 不错") == "你好世界，今天天气不错"


def test_clean_text_keeps_space_between_chinese_and_latin():
    assert clean_text("我用 Python 写代码") == "我用 Python 写代码"


def test_clean_text_handles_zero_width_chars():
    assert clean_text("\u200b你好\ufeff") == "你好"


def test_clean_text_empty_input():
    assert clean_text("") == ""
    assert clean_text("<|zh|><|NEUTRAL|>") == ""


def test_detect_language():
    assert detect_language("<|zh|><|NEUTRAL|>你好") == "zh"
    assert detect_language("<|en|>hello") == "en"
    assert detect_language("<|yue|>早晨") == "yue"
    assert detect_language("没有标签") == "auto"
    assert detect_language("") == "auto"


def test_join_texts_chinese_segments_without_space():
    assert join_texts(["今天天气不错。", "我们下午开会。"]) == "今天天气不错。我们下午开会。"
    assert join_texts(["你好，", "世界"]) == "你好，世界"


def test_join_texts_english_segments_with_space():
    assert join_texts(["Hello world.", "This is a test."]) == "Hello world. This is a test."
    assert join_texts(["one", "two"]) == "one two"


def test_join_texts_mixed_language_with_space():
    assert join_texts(["我用", "Python", "写代码。"]) == "我用 Python 写代码。"
    assert join_texts(["结果是", "OK"]) == "结果是 OK"


def test_join_texts_no_space_after_chinese_punctuation():
    assert join_texts(["你好。", "Hello"]) == "你好。Hello"


def test_join_texts_skips_empty_parts():
    assert join_texts([]) == ""
    assert join_texts(["", "   ", "你好", ""]) == "你好"
