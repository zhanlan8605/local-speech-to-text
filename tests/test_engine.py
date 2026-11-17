"""SenseVoice 引擎的分段逻辑测试。

不加载真实模型：用假模型对象替换 `_vad_model` / `_model`，
只验证「VAD 区间解析 → 按区间切音频 → 结果映射回时间戳」这条链路。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.asr.sensevoice import SenseVoiceEngine


class _FakeVad:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class _FakeAsr:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.batches: list[list] = []

    def generate(self, input, **kwargs):
        self.batches.append(list(input))
        return [{"text": text} for text in self.texts]


def _engine() -> SenseVoiceEngine:
    return SenseVoiceEngine(
        model_id="fake",
        vad_model_id="fake-vad",
        device="cpu",
        threads=1,
    )


def test_detect_speech_parses_milliseconds(sample_wav: Path):
    engine = _engine()
    engine._vad_model = _FakeVad(result=[{"value": [[0, 1000], [1500, 2500], [2500, 2500]]}])

    ranges = engine._detect_speech(sample_wav)

    assert ranges == [(0.0, 1.0), (1.5, 2.5)]


def test_detect_speech_returns_none_when_vad_fails(sample_wav: Path):
    engine = _engine()
    engine._vad_model = _FakeVad(error=RuntimeError("boom"))

    assert engine._detect_speech(sample_wav) is None


def test_detect_speech_without_vad_model(sample_wav: Path):
    engine = _engine()
    engine._vad_model = None

    assert engine._detect_speech(sample_wav) is None


def test_transcribe_ranges_maps_text_to_vad_timestamps(sample_wav: Path):
    engine = _engine()
    asr = _FakeAsr(["<|zh|><|NEUTRAL|>第一段。", "<|zh|><|NEUTRAL|>第二段。"])
    engine._model = asr

    segments, raw = engine._transcribe_ranges(asr, sample_wav, [(0.0, 1.0), (1.0, 2.0)], None)

    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.0, 1.0, "第一段。"),
        (1.0, 2.0, "第二段。"),
    ]
    assert "<|zh|>" in raw
    assert len(asr.batches) == 1
    assert len(asr.batches[0]) == 2


def test_transcribe_ranges_drops_too_short_segments(sample_wav: Path):
    engine = _engine()
    asr = _FakeAsr(["<|zh|>有效。"])
    engine._model = asr

    segments, _ = engine._transcribe_ranges(asr, sample_wav, [(0.0, 0.01), (1.0, 2.0)], None)

    assert [s.text for s in segments] == ["有效。"]
    assert len(asr.batches[0]) == 1


def test_transcribe_ranges_wraps_engine_error(sample_wav: Path):
    engine = _engine()
    engine._model = _FakeAsr([])

    class _Broken:
        def generate(self, **kwargs):
            raise RuntimeError("decode failed")

    with pytest.raises(RuntimeError, match="SenseVoice 推理失败"):
        engine._transcribe_ranges(_Broken(), sample_wav, [(0.0, 1.0)], None)
