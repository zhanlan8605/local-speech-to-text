"""pytest 公共夹具。

关键点：API 测试注入 FakeASREngine，不加载真实模型，
保证默认测试套件在几秒内跑完。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.asr.fake import FakeASREngine
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@pytest.fixture
def fake_engine() -> FakeASREngine:
    return FakeASREngine()


@pytest.fixture
def client(fake_engine: FakeASREngine):
    app = create_app(engine=fake_engine, start_warmup=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sample_wav() -> Path:
    path = SAMPLES / "chinese.wav"
    if not path.exists():
        pytest.skip("缺少 samples/chinese.wav，请先运行 scripts/download_samples.py")
    return path


@pytest.fixture
def sample_webm(sample_wav: Path, tmp_path: Path) -> Path:
    """把样例转成浏览器录音常用的 Opus/WebM，用于验证录音格式链路。"""
    import subprocess

    from app.audio import ffmpeg_path

    target = tmp_path / "recording.webm"
    proc = subprocess.run(
        [
            ffmpeg_path(),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(sample_wav),
            "-c:a",
            "libopus",
            "-b:a",
            "48k",
            str(target),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not target.exists():
        pytest.skip(f"当前 ffmpeg 不支持 libopus：{(proc.stderr or '').strip()[:200]}")
    return target
