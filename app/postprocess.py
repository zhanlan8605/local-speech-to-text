"""识别结果文本后处理。

SenseVoice 的原始输出带有富文本标签，例如：
    <|zh|><|NEUTRAL|><|Speech|><|withitn|>今天天气不错。
这里统一清掉标签并做基础排版归一化。
"""

from __future__ import annotations

import re

_RICH_TAG = re.compile(r"<\|[^|>]*\|>")
_LANG_TAG = re.compile(r"<\|(zh|en|ja|ko|yue)\|>")

# CJK 标点、统一表意文字、扩展 A、日文假名、韩文音节、全角符号
_CJK = r"\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\uff00-\uffef"
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# 拉丁字母/数字，以及拉丁文字里常见的收尾标点
_LATIN_ALNUM = re.compile(r"[0-9A-Za-z]")
_LATIN_CHAR = re.compile(r"[0-9A-Za-z\u00c0-\u024f\)\]\"'.,!?;:]")
# 只包含表意文字/假名/谚文，不含中文标点与全角符号
_CJK_LETTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def strip_tags(raw: str) -> str:
    """去掉所有 <|...|> 形式的模型标签。"""
    return _RICH_TAG.sub("", raw or "")


def detect_language(raw: str) -> str:
    """从原始输出里抓语种标签，抓不到返回 auto。"""
    match = _LANG_TAG.search(raw or "")
    return match.group(1) if match else "auto"


def clean_text(raw: str) -> str:
    """清理标签、零宽字符，并去掉中文字符之间多余的空格。"""
    text = strip_tags(raw)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = _CJK_SPACE.sub("", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def _needs_space(left: str, right: str) -> bool:
    """判断两个相邻字符之间是否需要补空格。

    规则：
    - 中文与拉丁字母相邻 → 补空格（"我用 Python"）
    - 拉丁字母之间、或英文句末标点后接英文 → 补空格
    - 中文与中文（含中文标点）之间 → 不补
    - 中文标点后接英文 → 不补（"你好。Hello"）
    """
    left_cjk = bool(_CJK_LETTER.fullmatch(left))
    right_cjk = bool(_CJK_LETTER.fullmatch(right))
    left_latin = bool(_LATIN_CHAR.fullmatch(left))

    if left_cjk and _LATIN_CHAR.fullmatch(right):
        return True
    if left_latin and right_cjk:
        return True
    if left_latin and _LATIN_ALNUM.fullmatch(right):
        return True
    return False


def join_texts(parts: list[str]) -> str:
    """拼接多个分段文本，按中英文规则补齐必要的空格。"""
    result = ""
    for part in parts:
        part = (part or "").strip()
        if not part:
            continue
        if result and _needs_space(result[-1], part[0]):
            result += " "
        result += part
    return result
