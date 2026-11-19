"""实时识别（WebSocket）测试。

默认套件不加载真实模型：用脚本化引擎模拟「VAD 判断句尾」，
验证分片累积、增量提交、强制提交、最终文本，以及 WebSocket 消息契约。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.asr.base import Segment, Transcript
from app.asr.fake import FakeASREngine
from app.config import settings
from app.main import create_app
from app.streaming import (
    MAX_FRAME_BYTES,
    SAMPLE_RATE,
    StreamingSession,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# ---------------------------------------------------------------- 测试替身
class _ScriptedEngine:
    """按 wav 实际时长返回分段，用来驱动「句尾提交」逻辑。

    默认行为：缓冲区 >= 3 秒时，把前 0.5 秒当作句尾静音，
    返回一个已经结束的句子；否则返回一个仍在进行中的句子。
    """

    name = "ScriptedEngine"
    loaded = True

    def __init__(self, text: str = "持续说话", sentence: str = "第一句。",
                 keep_open: bool = False) -> None:
        self.text = text
        self.sentence = sentence
        self.keep_open = keep_open
        self.calls: list[float] = []

    def warmup(self) -> None:  # pragma: no cover - 与真实接口保持一致
        pass

    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        duration = _wav_seconds(wav_path)
        self.calls.append(duration)
        if not self.keep_open and duration >= 3.0:
            return Transcript(
                text=self.sentence,
                language="zh",
                duration_s=duration,
                segments=[Segment(0.0, duration - 0.5, self.sentence)],
            )
        return Transcript(
            text=self.text,
            language="zh",
            duration_s=duration,
            segments=[Segment(0.0, duration, self.text)],
        )

    def unload(self) -> None:  # pragma: no cover
        pass


def _wav_seconds(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def _pcm(seconds: float, amplitude: int = 1200) -> bytes:
    """生成 16kHz/16bit 单声道 PCM，内容不重要，引擎是脚本化的。"""
    count = int(seconds * SAMPLE_RATE)
    return np.full(count, amplitude, dtype="<i2").tobytes()


# ---------------------------------------------------------------- 会话逻辑
def test_decode_returns_partial_while_sentence_is_open():
    session = StreamingSession(_ScriptedEngine())
    try:
        assert session.feed(_pcm(1.0)) is False
        assert session.ready_to_decode() is True

        update = session.decode()

        assert update.text == "持续说话"
        assert update.partial == "持续说话"
        assert update.committed == ""
        assert update.duration_s == pytest.approx(1.0, abs=0.02)
        assert update.segments
    finally:
        session.close()


def test_decode_commits_finished_sentence_and_trims_buffer():
    session = StreamingSession(_ScriptedEngine())
    try:
        session.feed(_pcm(3.0))
        update = session.decode()

        assert update.committed == "第一句。"
        assert update.partial == ""
        assert update.text == "第一句。"
        # 句尾 0.5s 仍在 pending 中，但已提交部分不会再被重复解码
        assert session.pending_seconds == pytest.approx(0.5, abs=0.03)
        assert update.duration_s == pytest.approx(3.0, abs=0.03)
    finally:
        session.close()


def test_pending_is_force_committed_when_buffer_grows():
    """连续讲话没有句尾静音时，也要定期提交，避免解码越来越慢。"""
    engine = _ScriptedEngine(keep_open=True)
    session = StreamingSession(engine)
    try:
        session.feed(_pcm(7.0))
        update = session.decode()

        assert update.committed == "持续说话"
        assert update.partial == ""
        assert session.pending_seconds < 0.1
        # 每次只解码当前缓冲，不会随录音变长而无限增长
        assert max(engine.calls) <= 7.05
    finally:
        session.close()


def test_finish_commits_remaining_partial():
    session = StreamingSession(_ScriptedEngine())
    try:
        session.feed(_pcm(1.0))
        session.decode()
        session.feed(_pcm(0.5))

        final = session.finish()

        assert final.text == "持续说话"
        assert final.partial == ""
        assert session.pending_seconds < 0.1
    finally:
        session.close()


class _FullDecodeEngine(_ScriptedEngine):
    """短缓冲只给半句，完整音频才给完整句子，用来验证停止时的整段重解码。"""

    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        duration = _wav_seconds(wav_path)
        self.calls.append(duration)
        if duration >= 6.0:
            return Transcript(
                text="完整句子。",
                language="zh",
                duration_s=duration,
                segments=[Segment(0.0, duration, "完整句子。")],
            )
        return Transcript(
            text="半句",
            language="zh",
            duration_s=duration,
            segments=[Segment(0.0, duration, "半句")],
        )


def test_finish_redecodes_full_audio_to_fix_split_sentences():
    engine = _FullDecodeEngine()
    session = StreamingSession(engine)
    try:
        session.feed(_pcm(4.0))
        partial = session.decode()
        # 增量结果已经把不完整的前缀返回出去
        assert "半句" in partial.text

        session.feed(_pcm(3.0))
        session.decode()

        final = session.finish()

        assert final.text == "完整句子。"
        assert "半句" not in final.text
        assert max(engine.calls) == pytest.approx(7.0, abs=0.05)
    finally:
        session.close()


def test_feed_stops_at_max_seconds():
    session = StreamingSession(_ScriptedEngine(), max_seconds=2.0)
    try:
        truncated = session.feed(_pcm(3.0))

        assert truncated is True
        assert session.duration_s == pytest.approx(2.0, abs=0.02)
    finally:
        session.close()


def test_feed_ignores_odd_trailing_byte():
    session = StreamingSession(_ScriptedEngine())
    try:
        assert session.feed(b"\x01") is False
        assert session.duration_s == 0.0

        session.feed(b"\x00\x00" * 1600)
        assert session.duration_s == pytest.approx(0.1, abs=0.01)
    finally:
        session.close()


# ---------------------------------------------------------------- WebSocket
class _EnergyVadEngine:
    """按音量切分语音区间，模拟真实 fsmn-vad 的行为。

    这样 WebSocket 测试就能验证「说完一句 → 提交 → 剩余部分继续识别」，
    而不是依赖固定文本的假引擎。
    """

    name = "EnergyVadEngine"
    loaded = True
    THRESHOLD = 500

    def __init__(self) -> None:
        self.calls: list[float] = []

    def warmup(self) -> None:  # pragma: no cover
        pass

    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        samples = _read_pcm(wav_path)
        duration = len(samples) / float(SAMPLE_RATE)
        self.calls.append(duration)
        ranges = _speech_ranges(samples, self.THRESHOLD)
        return Transcript(
            text="语音" * len(ranges),
            language="zh",
            duration_s=duration,
            segments=[Segment(start=s, end=e, text="语音") for s, e in ranges],
        )

    def unload(self) -> None:  # pragma: no cover
        pass


def _read_pcm(path: Path) -> np.ndarray:
    import wave

    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype="<i2")


def _speech_ranges(
    samples: np.ndarray, threshold: int, min_gap: float = 0.30, min_len: float = 0.10
) -> list[tuple[float, float]]:
    """用能量阈值找语音段，合并短静音，过滤过短片段。"""
    active = np.abs(samples) > threshold
    if not active.any():
        return []

    edges = np.diff(active.astype(np.int8))
    starts = [int(i) + 1 for i in np.where(edges == 1)[0]]
    ends = [int(i) + 1 for i in np.where(edges == -1)[0]]
    if active[0]:
        starts.insert(0, 0)
    if active[-1]:
        ends.append(len(samples))

    merged: list[tuple[int, int]] = []
    for start, end in zip(starts, ends):
        if merged and (start - merged[-1][1]) / SAMPLE_RATE < min_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    return [
        (start / SAMPLE_RATE, end / SAMPLE_RATE)
        for start, end in merged
        if (end - start) / SAMPLE_RATE >= min_len
    ]


def _speech_pcm(pattern: list[tuple[float, bool]], amplitude: int = 3000) -> bytes:
    """按 (时长, 是否说话) 生成 PCM。"""
    parts = [
        np.full(int(seconds * SAMPLE_RATE), amplitude if speech else 0, dtype="<i2")
        for seconds, speech in pattern
    ]
    return np.concatenate(parts).tobytes()


def _until(ws, kind: str, limit: int = 40) -> list[dict]:
    messages: list[dict] = []
    for _ in range(limit):
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == kind:
            return messages
    raise AssertionError(f"没有收到 {kind} 消息，实际收到：{messages}")


def test_websocket_commits_sentence_then_streams_final():
    engine = _EnergyVadEngine()
    app = create_app(engine=engine, start_warmup=False)
    # 说话 1s → 静音 1s → 说话 1s → 静音 1s
    pcm = _speech_pcm([(1.0, True), (1.0, False), (1.0, True), (1.0, False)])

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["sample_rate"] == SAMPLE_RATE
            assert ready["max_seconds"] == settings.stream_max_seconds
            assert ready["model"] == _EnergyVadEngine.name

            ws.send_json({"type": "start", "language": "auto"})
            assert ws.receive_json()["type"] == "started"

            # 第一帧：说完第一句并留出静音，服务端应提交这段文本
            ws.send_bytes(pcm[: len(pcm) // 2])
            partial = ws.receive_json()
            assert partial["type"] == "partial"
            assert partial["committed"] == "语音"
            assert partial["text"] == "语音"
            assert partial["segments"]

            # 第二帧：继续说话，直到 stop 才给出最终结果
            ws.send_bytes(pcm[len(pcm) // 2 :])
            update = ws.receive_json()
            assert update["type"] == "partial"
            assert update["committed"] == "语音语音"

            ws.send_json({"type": "stop"})
            final = _until(ws, "final")[-1]

    assert final["text"] == "语音语音"
    assert final["partial"] == ""
    assert final["duration_s"] == pytest.approx(4.0, abs=0.05)


def test_websocket_final_keeps_pending_speech():
    """停止时如果还在说话，也要把没等到静音的尾句一起提交。"""
    engine = _EnergyVadEngine()
    app = create_app(engine=engine, start_warmup=False)
    pcm = _speech_pcm([(1.0, False), (1.2, True)])

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            ws.receive_json()
            ws.send_bytes(pcm)
            partial = ws.receive_json()
            assert partial["type"] == "partial"
            assert partial["committed"] == ""
            assert partial["text"] == "语音"

            ws.send_json({"type": "stop"})
            final = _until(ws, "final")[-1]

    assert final["text"] == "语音"


def test_websocket_reports_engine_failure():
    engine = FakeASREngine(error=RuntimeError("boom"))
    app = create_app(engine=engine, start_warmup=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_bytes(_pcm(1.2))
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "流式识别失败" in message["detail"]


def test_websocket_rejects_unknown_control_message(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.receive_json()
        ws.send_text("not-json")
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "JSON" in message["detail"]


def test_websocket_rejects_oversized_frame(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.receive_json()
        ws.send_bytes(b"\x00" * (MAX_FRAME_BYTES + 2))
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "过大" in message["detail"]


def test_websocket_stop_without_audio_returns_empty_final(client):
    with client.websocket_connect("/ws/stream") as ws:
        ws.receive_json()
        ws.send_json({"type": "stop"})
        final = ws.receive_json()

    assert final["type"] == "final"
    assert final["text"] == ""
    assert final["duration_s"] == 0.0


def test_websocket_rejects_before_model_loaded():
    engine = FakeASREngine()
    engine._loaded = False
    app = create_app(engine=engine, start_warmup=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "模型尚未加载完成" in message["detail"]


def test_websocket_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "stream_max_seconds", 0)
    engine = FakeASREngine()
    app = create_app(engine=engine, start_warmup=False)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/stream") as ws:
            message = ws.receive_json()

    assert message["type"] == "error"
    assert "实时识别已关闭" in message["detail"]


def test_health_exposes_streaming_flags(client):
    data = client.get("/health").json()
    assert data["streaming_enabled"] is True
    assert data["stream_max_seconds"] == settings.stream_max_seconds
