"""测试用的假引擎。

不加载任何模型，让 API 层的测试在毫秒级完成。
故意保留和真实引擎一样的接口，避免测试与实现脱节。
"""

from __future__ import annotations

from pathlib import Path

from .base import Segment, Transcript


class FakeASREngine:
    name = "FakeEngine"

    def __init__(
        self,
        text: str = "这是一个测试。",
        language: str = "zh",
        duration_s: float = 4.0,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.language = language
        self.duration_s = duration_s
        self.error = error
        self.calls: list[tuple[Path, str | None]] = []
        self._loaded = True

    @property
    def loaded(self) -> bool:
        return self._loaded

    def warmup(self) -> None:  # pragma: no cover - 假引擎无需预热
        self._loaded = True

    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        self.calls.append((wav_path, language))
        if self.error is not None:
            raise self.error
        return Transcript(
            text=self.text,
            language=language or self.language,
            duration_s=self.duration_s,
            segments=[Segment(0.0, self.duration_s, self.text)],
        )

    def unload(self) -> None:
        self._loaded = False
