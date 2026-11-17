"""POST /api/transcribe 接口测试。

全部使用 FakeASREngine，不加载真实模型。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.asr.fake import FakeASREngine
from app.config import settings
from app.main import create_app

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _post_file(client, content: bytes, filename: str, *, language="auto", response_format="json"):
    return client.post(
        "/api/transcribe",
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
        data={"language": language},
        params={"response_format": response_format},
    )


def _post_sample(client, sample_wav: Path, **kwargs):
    return _post_file(client, sample_wav.read_bytes(), sample_wav.name, **kwargs)


def test_transcribe_returns_json(client, sample_wav, fake_engine):
    response = _post_sample(client, sample_wav)
    assert response.status_code == 200

    data = response.json()
    assert data["text"] == fake_engine.text
    assert data["language"] == "zh"
    assert data["duration_s"] > 0
    assert data["elapsed_s"] >= 0
    assert data["model"] == FakeASREngine.name
    assert len(data["segments"]) == 1
    assert set(data["segments"][0]) == {"start", "end", "text"}


def test_transcribe_passes_explicit_language(client, sample_wav, fake_engine):
    response = _post_sample(client, sample_wav, language="en")
    assert response.status_code == 200
    assert fake_engine.calls[-1][1] == "en"
    assert response.json()["language"] == "en"


def test_transcribe_auto_language_passes_none(client, sample_wav, fake_engine):
    _post_sample(client, sample_wav, language="auto")
    assert fake_engine.calls[-1][1] is None


def test_transcribe_txt_format(client, sample_wav, fake_engine):
    response = _post_sample(client, sample_wav, response_format="txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == fake_engine.text


def test_transcribe_accepts_webm_recording(client, sample_webm, fake_engine):
    """录音功能上传的是浏览器生成的 Opus/WebM，接口必须直接接受。"""
    response = _post_file(client, sample_webm.read_bytes(), "recording-20261116-210000.webm")
    assert response.status_code == 200
    assert response.json()["text"] == fake_engine.text
    assert response.json()["duration_s"] > 1


def test_transcribe_srt_format(client, sample_wav):
    response = _post_sample(client, sample_wav, response_format="srt")
    assert response.status_code == 200
    assert "-->" in response.text
    assert "00:00:00,000 --> 00:00:04,000" in response.text


def test_rejects_invalid_language(client, sample_wav):
    response = _post_sample(client, sample_wav, language="fr")
    assert response.status_code == 400
    assert "语言" in response.json()["detail"]


def test_rejects_invalid_format(client, sample_wav):
    response = _post_sample(client, sample_wav, response_format="xml")
    assert response.status_code == 400


def test_rejects_unsupported_extension(client):
    response = _post_file(client, b"hello", "notes.txt")
    assert response.status_code == 415


def test_rejects_empty_file(client):
    response = _post_file(client, b"", "empty.wav")
    assert response.status_code == 400


def test_rejects_too_large_file(client, sample_wav, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    response = _post_sample(client, sample_wav)
    assert response.status_code == 413


def test_rejects_file_over_configured_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = _post_file(client, b"x" * (1024 * 1024 + 1), "big.wav")
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_rejects_oversized_content_length_before_parsing(client, monkeypatch):
    """Content-Length 已超限时，中间件应在 multipart 解析前直接拒绝。"""
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    response = client.post(
        "/api/transcribe",
        content=b"x",
        headers={
            "content-type": "application/octet-stream",
            "content-length": str(2 * 1024 * 1024),
        },
    )
    assert response.status_code == 413
    assert "1MB" in response.json()["detail"]


def test_engine_failure_returns_500():
    engine = FakeASREngine(error=RuntimeError("boom"))
    app = create_app(engine=engine, start_warmup=False)

    with TestClient(app) as client:
        response = _post_file(client, _fake_wav_bytes(), "chinese.wav")

    assert response.status_code == 500
    assert "识别失败" in response.json()["detail"]


def _fake_wav_bytes() -> bytes:
    """构造一个极小的合法 wav，避免依赖 samples 目录。"""
    import struct

    sample_rate = 16000
    samples = b"\x00\x00" * 1600  # 0.1s 静音
    data_size = len(samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        sample_rate,
        sample_rate * 2,
        2,
        16,
        b"data",
        data_size,
    )
    return header + samples
