"""模型路径解析测试（不依赖真实模型）。"""

from __future__ import annotations

from pathlib import Path

from app.config import resolve_local_model


def _make_model(root: Path, cache_name: str, snapshot: str = "master") -> Path:
    target = root / "models" / cache_name / "snapshots" / snapshot
    target.mkdir(parents=True)
    (target / "model.pt").write_bytes(b"fake")
    return target


def test_resolve_modelscope_cache_layout(tmp_path: Path):
    snapshot = _make_model(tmp_path, "iic--SenseVoiceSmall")

    assert resolve_local_model("iic/SenseVoiceSmall", tmp_path) == str(snapshot)


def test_resolve_picks_newest_snapshot(tmp_path: Path):
    older = _make_model(tmp_path, "iic--SenseVoiceSmall", "v1")
    newer = _make_model(tmp_path, "iic--SenseVoiceSmall", "v2")
    import os
    import time

    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))

    assert resolve_local_model("iic/SenseVoiceSmall", tmp_path) == str(newer)


def test_resolve_vad_alias(tmp_path: Path):
    snapshot = _make_model(tmp_path, "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch")

    assert resolve_local_model("fsmn-vad", tmp_path) == str(snapshot)


def test_resolve_manual_layout(tmp_path: Path):
    manual = tmp_path / "iic" / "SenseVoiceSmall"
    manual.mkdir(parents=True)

    assert resolve_local_model("iic/SenseVoiceSmall", tmp_path) == str(manual)


def test_resolve_existing_directory(tmp_path: Path):
    local = tmp_path / "my-model"
    local.mkdir()

    assert resolve_local_model(str(local), tmp_path) == str(local)


def test_resolve_returns_id_when_missing(tmp_path: Path):
    assert resolve_local_model("iic/NotDownloaded", tmp_path) == "iic/NotDownloaded"
    assert resolve_local_model("", tmp_path) == ""
