"""真实模型冒烟测试（默认跳过）。

运行：
    pytest -m slow -v

前置条件：
    1. 已安装 torch / funasr
    2. 模型已下载（首次运行会自动下载约 940MB）
    3. samples/ 下有音频与 reference.txt
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest

from app.config import SAMPLES_DIR, settings

pytestmark = pytest.mark.slow


def _normalize(text: str) -> str:
    """去掉标点/空白并转小写，只比较可听辨的字符。"""
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def _load_reference() -> dict[str, tuple[str, str]]:
    path = SAMPLES_DIR / "reference.txt"
    if not path.exists():
        pytest.skip("缺少 samples/reference.txt")

    items: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        name, language, text = (part.strip() for part in parts)
        items[name] = (language, text)
    return items


@pytest.fixture(scope="module")
def engine():
    from app.asr.sensevoice import SenseVoiceEngine

    eng = SenseVoiceEngine(
        model_id=settings.asr_model,
        vad_model_id=settings.vad_model or None,
        device=settings.device,
        threads=settings.threads,
        punc_model_id=settings.punc_model or None,
    )
    eng.warmup()
    if not eng.loaded:
        pytest.skip(f"模型未能加载：{eng.load_error}")
    try:
        yield eng
    finally:
        eng.unload()


def test_real_model_matches_reference(engine):
    reference = _load_reference()
    assert reference, "reference.txt 为空"

    failures: list[str] = []
    report: list[str] = []

    for name, (expected_lang, expected_text) in reference.items():
        audio = SAMPLES_DIR / name
        if not audio.exists():
            failures.append(f"{name}: 音频文件缺失")
            continue

        transcript = engine.transcribe(audio, None)
        ratio = difflib.SequenceMatcher(
            None, _normalize(transcript.text), _normalize(expected_text)
        ).ratio()
        report.append(
            f"{name}: lang={transcript.language} ratio={ratio:.3f}\n"
            f"  expect={expected_text}\n  actual={transcript.text}"
        )
        if transcript.language != expected_lang:
            failures.append(f"{name}: 语种期望 {expected_lang}，实际 {transcript.language}")
        if ratio < 0.6:
            failures.append(f"{name}: 相似度 {ratio:.3f} 低于阈值 0.6")

    print("\n".join(report))
    assert not failures, "\n".join(failures)
