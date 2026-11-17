"""ASR 引擎抽象。

把具体推理框架（funasr / sherpa-onnx / 云端 API）隔离在接口之后，
上层 API 与测试只依赖这里的协议，方便日后替换实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Segment:
    """带时间戳的一小段识别结果，单位秒。"""

    start: float
    end: float
    text: str


@dataclass
class Transcript:
    text: str
    language: str
    duration_s: float
    segments: list[Segment] = field(default_factory=list)


@runtime_checkable
class ASREngine(Protocol):
    """所有引擎必须满足的最小接口。"""

    name: str

    @property
    def loaded(self) -> bool:
        """模型是否已经加载完成（用于 /health，不应阻塞）。"""

    def warmup(self) -> None:
        """在后台线程中预加载模型，失败时记录日志而不是崩溃。"""

    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        """识别一个 16kHz 单声道 wav 文件。"""

    def unload(self) -> None:
        """释放模型占用的内存。"""
