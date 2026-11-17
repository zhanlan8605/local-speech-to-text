"""音频预处理测试（依赖 imageio-ffmpeg 自带的 ffmpeg）。"""

from __future__ import annotations

import pytest
import soundfile as sf

from app.audio import AudioConversionError, is_supported_filename, to_wav16k, wav_duration


def test_is_supported_filename():
    assert is_supported_filename("a.wav")
    assert is_supported_filename("A.MP3")
    assert is_supported_filename("meeting.mp4")
    assert is_supported_filename("recording-20261116.webm")
    assert not is_supported_filename("notes.txt")
    assert not is_supported_filename("")


def test_convert_opus_webm_to_16k(sample_webm, tmp_path):
    """浏览器 MediaRecorder 默认产出 Opus/WebM，必须能走同一条转码链路。"""
    dst = tmp_path / "out.wav"
    to_wav16k(sample_webm, dst)

    info = sf.info(str(dst))
    assert info.samplerate == 16000
    assert info.channels == 1
    assert wav_duration(dst) > 1


def test_convert_sample_to_16k_mono(sample_wav, tmp_path):
    dst = tmp_path / "out.wav"
    to_wav16k(sample_wav, dst)

    info = sf.info(str(dst))
    assert info.samplerate == 16000
    assert info.channels == 1
    assert wav_duration(dst) > 0


def test_corrupt_audio_raises(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"this is definitely not audio")

    with pytest.raises(AudioConversionError):
        to_wav16k(bad, tmp_path / "out.wav")


def test_wav_duration_missing_file_returns_zero(tmp_path):
    assert wav_duration(tmp_path / "missing.wav") == 0.0
