"""实时（流式）识别：WebSocket 分片 + VAD 增量提交。

SenseVoiceSmall 本身不是流式模型，这里用「滚动重解码」实现近实时效果：

1. 浏览器把麦克风音频降采样成 16kHz / 16bit / 单声道 PCM，按约 128ms 一帧
   通过 WebSocket 发送（二进制帧，不加任何容器格式）；
2. 服务端把「尚未提交」的音频累积成 pending 缓冲，每累计约 1 秒音频重新解码一次；
3. 解码复用 ``ASREngine.transcribe``（内部已含 fsmn-vad 分段）：
   - 若 VAD 发现 pending 尾部有足够长的静音，说明这句话已经说完，
     把已完成的语音段写入 committed，并丢弃对应 PCM；
   - 否则把整段结果作为 partial 返回，前端以浅色实时显示；
4. pending 超过 ``MAX_PENDING_SECONDS`` 时强制提交，保证单次解码时长与内存有上限，
   长时间连续讲话也不会越解码越慢；
5. 停止录音时对整段音频（最多 60 秒，约 2MB）重新解码一次，修正增量提交可能
   切开的句子，最终文本与 ``/api/transcribe`` 的质量一致。

因此解码总成本约为音频时长的常数倍，而不是「每次重解全部历史音频」的平方增长。
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from .asr.base import ASREngine, Segment
from .config import settings
from .postprocess import join_texts

logger = logging.getLogger(__name__)

# 音频约定：与 SenseVoice / fsmn-vad 的训练采样率一致
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2

# 少于该时长不触发解码，避免把半个字送去识别
MIN_DECODE_SECONDS = 0.8
# 距上次解码至少新增这么多音频才再次解码，控制 CPU 占用
DECODE_INTERVAL_SECONDS = 1.0
# pending 上限：超过则强制提交，避免连续讲话时单次解码越来越慢
MAX_PENDING_SECONDS = 6.0
# pending 尾部静音超过该时长，视为一句话结束
END_SILENCE_SECONDS = 0.45
# 完全没有语音时，直接丢弃这么长的缓冲
MAX_SILENCE_SECONDS = 12.0
# 单次流式识别最长时长（秒）
DEFAULT_MAX_SECONDS = 60.0
# 单个 WebSocket 帧上限，正常一帧约 4KB
MAX_FRAME_BYTES = 512 * 1024


@dataclass
class StreamUpdate:
    """一次解码后的完整状态，可直接序列化成 WS 消息。"""

    text: str
    committed: str
    partial: str
    duration_s: float
    elapsed_s: float
    segments: list[Segment] = field(default_factory=list)

    def to_message(self, kind: str) -> dict:
        return {
            "type": kind,
            "text": self.text,
            "committed": self.committed,
            "partial": self.partial,
            "duration_s": round(self.duration_s, 3),
            "elapsed_s": round(self.elapsed_s, 3),
            "segments": [
                {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text}
                for s in self.segments
            ],
        }


class StreamingSession:
    """一次流式识别会话：负责缓冲 PCM、调度解码、维护已提交文本。"""

    def __init__(
        self,
        engine: ASREngine,
        language: str | None = None,
        sample_rate: int = SAMPLE_RATE,
        max_seconds: float = DEFAULT_MAX_SECONDS,
    ) -> None:
        self.engine = engine
        self.language = language
        self.sample_rate = sample_rate
        self._max_seconds = max(0.5, float(max_seconds))

        self._pcm = bytearray()
        self._all_pcm = bytearray()  # 本次会话的完整音频，停止时整体重解码
        self._accepted = 0  # 本次会话已接收的字节数，用于总时长上限

        self._committed_parts: list[str] = []
        self._committed_segments: list[Segment] = []
        self._partial = ""
        self._partial_segments: list[Segment] = []

        self._base_offset = 0.0  # pending 起点在整段录音中的秒数
        self._decoded_at = 0.0  # 上次解码时已累计的总时长
        self._elapsed = 0.0  # 累计解码耗时（秒）

        self._tmpdir = tempfile.TemporaryDirectory(prefix="a2t-stream-")
        self._wav_path = Path(self._tmpdir.name) / "pending.wav"

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def max_seconds(self) -> float:
        return self._max_seconds

    def _pending_seconds(self) -> float:
        return len(self._pcm) / float(self.sample_rate * BYTES_PER_SAMPLE)

    @property
    def pending_seconds(self) -> float:
        """尚未提交（即还可能在变化）的音频时长。"""
        return self._pending_seconds()

    @property
    def duration_s(self) -> float:
        """当前累计音频时长（含已提交与未提交部分）。"""
        return self._base_offset + self._pending_seconds()

    @property
    def committed_text(self) -> str:
        return join_texts(self._committed_parts)

    @property
    def text(self) -> str:
        return join_texts([*self._committed_parts, self._partial])

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------
    def feed(self, data: bytes) -> bool:
        """追加 PCM16LE 单声道音频，返回是否因超时长上限而丢弃了数据。"""
        if not data:
            return False
        if len(data) % BYTES_PER_SAMPLE:
            data = data[:-BYTES_PER_SAMPLE]

        limit = int(self._max_seconds * self.sample_rate) * BYTES_PER_SAMPLE
        room = limit - self._accepted
        if room <= 0:
            return bool(data)

        if len(data) > room:
            data = data[: room - (room % BYTES_PER_SAMPLE)]
            self._accepted = limit
            self._pcm.extend(data)
            self._all_pcm.extend(data)
            return True

        self._accepted += len(data)
        self._pcm.extend(data)
        self._all_pcm.extend(data)
        return False

    def ready_to_decode(self) -> bool:
        """是否应该触发一次增量解码（按音频时长而不是墙钟，便于测试复现）。"""
        if self._pending_seconds() < MIN_DECODE_SECONDS:
            return False
        return (self.duration_s - self._decoded_at) >= DECODE_INTERVAL_SECONDS

    # ------------------------------------------------------------------
    # 解码
    # ------------------------------------------------------------------
    def decode(self) -> StreamUpdate:
        """增量解码一次：尽量把已说完的句子提交，剩下的作为 partial。"""
        return self._run_decode(final=False)

    def finish(self) -> StreamUpdate:
        """停止录音：对整段音频重新解码一次，得到与上传识别一致的最终文本。

        增量解码为了低延迟会频繁提交，可能把一句话从中间切开；停止时用完整
        音频（最多 ``max_seconds``，约 2MB）再解码一次，既修正切分位置，也
        保证最终结果与 ``/api/transcribe`` 的质量一致。
        """
        if self._all_pcm:
            started = time.perf_counter()
            self._decoded_at = self.duration_s
            transcript = self._transcribe_pcm(bytes(self._all_pcm))
            self._elapsed += time.perf_counter() - started

            segments = [
                Segment(start=s.start, end=s.end, text=s.text)
                for s in transcript.segments
                if (s.text or "").strip()
            ]
            if not segments and (transcript.text or "").strip():
                # VAD 没有给出区间时，退化为整段一条
                segments = [
                    Segment(0.0, self.duration_s, transcript.text.strip())
                ]

            if segments:
                self._committed_parts = []
                self._committed_segments = []
                self._commit(segments)
                self._partial = ""
                self._partial_segments = []
                self._drop_prefix(self._pending_seconds())
                return self._final_update()

        # 没有任何音频，或整段解码没有得到文本：沿用增量结果
        self._run_decode(final=True)
        self._partial = ""
        self._partial_segments = []
        return self._final_update()

    def _run_decode(self, final: bool) -> StreamUpdate:
        pending_before = self._pending_seconds()
        if pending_before <= 0.0:
            return self._update(0.0)

        started = time.perf_counter()
        self._decoded_at = self.duration_s
        transcript = self._transcribe_pending()
        elapsed = time.perf_counter() - started
        self._elapsed += elapsed

        base = self._base_offset
        segments = transcript.segments
        absolute = [
            Segment(start=s.start + base, end=s.end + base, text=s.text) for s in segments
        ]

        if final:
            if absolute:
                self._commit(absolute)
            elif self._partial and not self._committed_parts:
                # VAD 丢掉尾音时的保底：沿用上一次 partial 文本
                self._commit([Segment(base, base + pending_before, self._partial)])
            self._drop_prefix(pending_before)
            self._partial = ""
            self._partial_segments = []
            return self._update(elapsed)

        cut = self._complete_count(segments, pending_before)
        if cut:
            self._commit(absolute[:cut])
            self._drop_prefix(segments[cut - 1].end)
            self._partial_segments = absolute[cut:]
        else:
            self._partial_segments = absolute
        self._partial = join_texts([s.text for s in self._partial_segments])

        # 长时间连续讲话：强制提交当前 pending，避免单次解码越拖越慢
        if self._pending_seconds() >= MAX_PENDING_SECONDS:
            self._commit(self._partial_segments)
            self._drop_prefix(self._pending_seconds())
            self._partial = ""
            self._partial_segments = []
        elif not segments and self._pending_seconds() >= MAX_SILENCE_SECONDS:
            # 整段静音，直接丢弃，避免内存无意义增长
            self._drop_prefix(self._pending_seconds())

        return self._update(elapsed)

    def _complete_count(self, segments: list[Segment], pending_s: float) -> int:
        """找出「已经说完」的前缀段数：段尾距缓冲区末尾还有足够静音。"""
        limit = pending_s - END_SILENCE_SECONDS
        count = 0
        for seg in segments:
            if seg.end <= seg.start or seg.end > limit:
                break
            count += 1
        return count

    def _transcribe_pending(self):
        return self._transcribe_pcm(bytes(self._pcm))

    def _transcribe_pcm(self, pcm: bytes):
        with wave.open(str(self._wav_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(BYTES_PER_SAMPLE)
            handle.setframerate(self.sample_rate)
            handle.writeframes(pcm)
        return self.engine.transcribe(self._wav_path, self.language)

    # ------------------------------------------------------------------
    # 内部状态维护
    # ------------------------------------------------------------------
    def _commit(self, segments: list[Segment]) -> None:
        for seg in segments:
            text = (seg.text or "").strip()
            if not text:
                continue
            self._committed_parts.append(text)
            self._committed_segments.append(
                Segment(start=seg.start, end=seg.end, text=text)
            )

    def _drop_prefix(self, seconds: float) -> None:
        """丢弃 pending 开头 seconds 秒音频，并推进绝对时间基准。"""
        if seconds <= 0:
            return
        bytes_to_drop = int(seconds * self.sample_rate) * BYTES_PER_SAMPLE
        bytes_to_drop = min(bytes_to_drop, len(self._pcm))
        if bytes_to_drop <= 0:
            return
        del self._pcm[:bytes_to_drop]
        self._base_offset += bytes_to_drop / float(self.sample_rate * BYTES_PER_SAMPLE)

    def _update(self, elapsed: float) -> StreamUpdate:
        return StreamUpdate(
            text=self.text,
            committed=self.committed_text,
            partial=self._partial,
            duration_s=self.duration_s,
            elapsed_s=self._elapsed,
            segments=[*self._committed_segments, *self._partial_segments],
        )

    def _final_update(self) -> StreamUpdate:
        return StreamUpdate(
            text=self.committed_text,
            committed=self.committed_text,
            partial="",
            duration_s=self.duration_s,
            elapsed_s=self._elapsed,
            segments=list(self._committed_segments),
        )

    def close(self) -> None:
        try:
            self._tmpdir.cleanup()
        except Exception:  # pragma: no cover - 退出时的兜底清理
            pass


# ----------------------------------------------------------------------
# WebSocket 接口
# ----------------------------------------------------------------------
router = APIRouter()


def _parse_control(raw: str) -> dict | str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "无法解析的控制消息，应为 JSON"
    if not isinstance(payload, dict):
        return "控制消息必须是 JSON 对象"
    return payload


@router.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket) -> None:
    """实时识别接口。

    客户端 → 服务端：
        文本 ``{"type": "start", "language": "auto"}``（可选，用于指定语种）
        二进制 PCM16LE、16kHz、单声道音频帧
        文本 ``{"type": "stop"}`` 结束本次识别
    服务端 → 客户端：
        ``{"type": "ready"}`` / ``{"type": "started"}``
        ``{"type": "partial", "text", "committed", "partial", ...}``
        ``{"type": "final", ...}`` / ``{"type": "error", "detail"}``
    """
    await websocket.accept()
    engine = websocket.app.state.engine
    session: StreamingSession | None = None

    try:
        if not getattr(engine, "loaded", False):
            await websocket.send_json(
                {"type": "error", "detail": "模型尚未加载完成，请稍候再试"}
            )
            await websocket.close(code=1013)
            return

        if not settings.streaming_enabled:
            await websocket.send_json(
                {"type": "error", "detail": "实时识别已关闭（A2T_STREAM_MAX_SECONDS=0）"}
            )
            await websocket.close(code=1013)
            return

        await websocket.send_json(
            {
                "type": "ready",
                "model": getattr(engine, "name", settings.backend),
                "sample_rate": SAMPLE_RATE,
                "max_seconds": settings.stream_max_seconds,
            }
        )

        finished = False
        while not finished:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

            data = message.get("bytes")
            if data is not None:
                if len(data) > MAX_FRAME_BYTES:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": f"音频帧过大：{len(data)} 字节，上限 {MAX_FRAME_BYTES} 字节",
                        }
                    )
                    finished = True
                    break
                if session is None:
                    session = StreamingSession(
                        engine, max_seconds=settings.stream_max_seconds
                    )
                truncated = session.feed(data)
                if truncated:
                    finished = True
                elif session.ready_to_decode():
                    update = await run_in_threadpool(session.decode)
                    await websocket.send_json(update.to_message("partial"))
                if session.duration_s >= session.max_seconds:
                    finished = True
                continue

            raw = message.get("text")
            if raw is None:
                continue
            payload = _parse_control(raw)
            if isinstance(payload, str):
                await websocket.send_json({"type": "error", "detail": payload})
                continue

            kind = payload.get("type")
            if kind == "start":
                language = payload.get("language") or "auto"
                language = str(language).lower()
                if language not in settings.allowed_languages:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "detail": f"不支持的语言：{language}，可选："
                            f"{', '.join(settings.allowed_languages)}",
                        }
                    )
                    continue
                if session is None:
                    session = StreamingSession(
                        engine,
                        language=None if language == "auto" else language,
                        max_seconds=settings.stream_max_seconds,
                    )
                await websocket.send_json(
                    {
                        "type": "started",
                        "language": language,
                        "sample_rate": SAMPLE_RATE,
                        "max_seconds": settings.stream_max_seconds,
                    }
                )
            elif kind == "stop":
                finished = True
            elif kind == "ping":
                await websocket.send_json({"type": "pong"})
            else:
                await websocket.send_json(
                    {"type": "error", "detail": f"未知消息类型：{kind}"}
                )

        if session is None:
            await websocket.send_json(
                StreamUpdate(
                    text="",
                    committed="",
                    partial="",
                    duration_s=0.0,
                    elapsed_s=0.0,
                ).to_message("final")
            )
        else:
            update = await run_in_threadpool(session.finish)
            await websocket.send_json(update.to_message("final"))

    except WebSocketDisconnect:
        logger.info("流式识别连接已断开")
    except Exception as exc:  # 推理异常、客户端中途关闭等
        logger.exception("流式识别失败")
        try:
            await websocket.send_json({"type": "error", "detail": f"流式识别失败：{exc}"})
        except Exception:
            pass
    finally:
        if session is not None:
            session.close()
        try:
            await websocket.close()
        except Exception:
            pass
