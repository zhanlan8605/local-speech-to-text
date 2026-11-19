"""真实模型的实时识别验证（默认跳过）。

运行：
    python -m pytest -m slow -v

前置条件：
    1. 已安装 torch / funasr；
    2. 模型已缓存（首次运行会自动下载约 900MB）；
    3. samples/ 下有中英文样例与 reference.txt。

这里不启动 HTTP 服务，直接驱动 ``StreamingSession`` + 真实引擎，
验证「分片喂入 → 中途出现文本 → 停止后得到最终文本」这条链路。
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.config import SAMPLES_DIR
from app.streaming import SAMPLE_RATE, StreamingSession

pytestmark = pytest.mark.slow

# 与前端一致的帧长：2048 个采样点 ≈ 128ms
FRAME_BYTES = 2048 * 2


def _load_pcm(path: Path) -> bytes:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        target = int(round(len(mono) * SAMPLE_RATE / rate))
        mono = np.interp(
            np.linspace(0, len(mono) - 1, target), np.arange(len(mono)), mono
        )
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


def _normalize(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", (text or "").lower())


def _reference(name: str) -> str:
    path = SAMPLES_DIR / "reference.txt"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 2)
        if len(parts) == 3 and parts[0].strip() == name:
            return parts[2].strip()
    return ""


@pytest.mark.parametrize("name", ["chinese.wav", "english.wav"])
def test_real_model_streams_partial_then_final(real_engine, name: str):
    path = SAMPLES_DIR / name
    if not path.exists():
        pytest.skip(f"缺少样例 {path}")

    engine = real_engine
    pcm = _load_pcm(path)
    session = StreamingSession(engine)
    updates = []
    appeared_before_end = False
    fed = 0
    try:
        while fed < len(pcm):
            chunk = pcm[fed : fed + FRAME_BYTES]
            fed += len(chunk)
            session.feed(chunk)
            if session.ready_to_decode():
                update = session.decode()
                updates.append(update)
                if fed < len(pcm) and update.text.strip():
                    appeared_before_end = True
        final = session.finish()
    finally:
        session.close()

    assert updates, "流式过程中没有产生任何中间结果"
    assert appeared_before_end, "所有文本都是停止后才出现的，不符合实时预期"
    assert final.partial == ""
    assert final.text.strip(), "最终文本为空"
    assert final.duration_s == pytest.approx(len(pcm) / 2 / SAMPLE_RATE, abs=0.3)

    expected = _reference(name)
    if expected:
        ratio = difflib.SequenceMatcher(
            None, _normalize(expected), _normalize(final.text)
        ).ratio()
        assert ratio >= 0.6, f"{name} 流式结果与参考文本差异过大：{ratio:.3f}\n{final.text}"
