"""SRT 字幕生成。"""

from __future__ import annotations

from .asr.base import Segment


def format_timestamp(seconds: float) -> str:
    """把秒转换为 SRT 的 HH:MM:SS,mmm。"""
    if seconds is None or seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, total_ms = divmod(total_ms, 3_600_000)
    minutes, total_ms = divmod(total_ms, 60_000)
    secs, millis = divmod(total_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(segments: list[Segment], duration_s: float | None = None) -> str:
    """把分段结果渲染为 SRT。没有有效时间戳时退化为整段一条字幕。"""
    usable = [seg for seg in segments if seg.text.strip()]
    if usable and all(seg.end <= 0 for seg in usable):
        # VAD 未提供时间戳：按总时长铺一条字幕，避免输出全为 00:00:00,000
        total = duration_s or 0.0
        usable = [Segment(0.0, total, "".join(seg.text for seg in usable))]

    if not usable and duration_s:
        usable = [Segment(0.0, duration_s, "")]

    blocks = []
    for index, seg in enumerate(usable, start=1):
        start = seg.start if seg.start >= 0 else 0.0
        end = seg.end if seg.end > start else start + 0.01
        blocks.append(
            f"{index}\n{format_timestamp(start)} --> {format_timestamp(end)}\n{seg.text.strip()}\n"
        )
    return "\n".join(blocks)
