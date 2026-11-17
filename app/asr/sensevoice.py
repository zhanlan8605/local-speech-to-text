"""SenseVoiceSmall 引擎实现（FunASR）。

设计要点：
1. 懒加载：进程启动后由后台线程预热，首个请求不必等模型初始化。
2. 单锁串行：CPU 上并发推理只会互相拖慢，因此用一把可重入锁把
   「加载 + 推理」串行化，第二个请求排队等待。
3. 显式 VAD 分段：FunASR 1.4.x 的 SenseVoice 管线会把整段音频合并成
   一条结果、不返回时间戳，因此这里单独加载 fsmn-vad，先取到语音区间，
   再按区间批量识别，既控制内存又能产出可用的 SRT 时间轴。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..audio import wav_duration
from ..config import resolve_local_model
from ..postprocess import clean_text, detect_language, join_texts
from .base import Segment, Transcript

logger = logging.getLogger(__name__)

# 单批累计语音时长上限（秒）与段数上限，避免长音频一次性占满内存
_MAX_BATCH_SECONDS = 60.0
_MAX_BATCH_ITEMS = 16
# 忽略过短的 VAD 片段（秒）
_MIN_SEGMENT_SECONDS = 0.05


class SenseVoiceEngine:
    name = "SenseVoiceSmall"

    def __init__(
        self,
        model_id: str,
        vad_model_id: str | None = None,
        device: str = "cpu",
        threads: int = 4,
        punc_model_id: str | None = None,
        batch_size_s: int = 60,
    ) -> None:
        self.model_id = model_id
        self.vad_model_id = vad_model_id
        self.device = device
        self.threads = threads
        self.punc_model_id = punc_model_id or None
        self.batch_size_s = batch_size_s

        self._model = None
        self._vad_model = None
        self._lock = threading.RLock()
        self._load_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def warmup(self) -> None:
        try:
            self._load()
        except Exception as exc:  # 预热失败不应让进程退出，交由 /health 暴露
            self._load_error = str(exc)
            logger.exception("SenseVoice 模型加载失败")

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._vad_model = None
            self._load_error = None
            try:
                import gc

                gc.collect()
            except Exception:  # pragma: no cover
                pass

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model

            import torch

            torch.set_num_threads(max(1, self.threads))

            from funasr import AutoModel

            common = {
                "device": self.device,
                "disable_update": True,
                "disable_pbar": True,
                "disable_log": True,
            }

            logger.info("开始加载模型 %s (device=%s, threads=%s)", self.model_id, self.device, self.threads)
            self._model = AutoModel(model=resolve_local_model(self.model_id), **common)

            if self.vad_model_id:
                logger.info("加载 VAD 模型 %s", self.vad_model_id)
                self._vad_model = AutoModel(model=resolve_local_model(self.vad_model_id), **common)

            self._load_error = None
            logger.info("模型加载完成：%s", self.model_id)
            return self._model

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def transcribe(self, wav_path: Path, language: str | None = None) -> Transcript:
        with self._lock:
            model = self._load()
            duration = wav_duration(wav_path)

            ranges = self._detect_speech(wav_path)
            if ranges is None:  # VAD 不可用或失败：退化为整段识别
                segments, raw_text = self._transcribe_whole(model, wav_path, language)
            else:
                segments, raw_text = self._transcribe_ranges(model, wav_path, ranges, language)

        detected = detect_language(raw_text)
        if detected == "auto" and language:
            detected = language

        return Transcript(
            text=join_texts([seg.text for seg in segments]),
            language=detected,
            duration_s=duration,
            segments=segments,
        )

    def _detect_speech(self, wav_path: Path) -> list[tuple[float, float]] | None:
        """返回语音区间（秒）。VAD 不可用时返回 None。"""
        if self._vad_model is None:
            return None
        try:
            result = self._vad_model.generate(input=str(wav_path), cache={}, is_final=True)
        except Exception:
            logger.exception("VAD 分段失败，退化为整段识别")
            return None

        if not result:
            return []
        ranges = result[0].get("value") or []
        parsed: list[tuple[float, float]] = []
        for item in ranges:
            try:
                start_ms, end_ms = float(item[0]), float(item[1])
            except (TypeError, IndexError, ValueError):
                continue
            if end_ms <= start_ms:
                continue
            parsed.append((start_ms / 1000.0, end_ms / 1000.0))
        return parsed

    def _transcribe_ranges(
        self,
        model,
        wav_path: Path,
        ranges: list[tuple[float, float]],
        language: str | None,
    ) -> tuple[list[Segment], str]:
        import soundfile as sf

        segments: list[Segment] = []
        tags: list[str] = []

        with sf.SoundFile(str(wav_path)) as handle:
            rate = handle.samplerate
            batch: list = []
            meta: list[tuple[float, float]] = []
            batch_seconds = 0.0

            def flush() -> None:
                nonlocal batch, meta, batch_seconds
                if not batch:
                    return
                try:
                    results = model.generate(
                        input=batch,
                        cache={},
                        language=language or "auto",
                        use_itn=True,
                        batch_size_s=self.batch_size_s,
                    )
                except Exception as exc:
                    raise RuntimeError(f"SenseVoice 推理失败: {exc}") from exc

                for index, item in enumerate(results or []):
                    if index >= len(meta) or not isinstance(item, dict):
                        break
                    raw = str(item.get("text") or "")
                    if raw:
                        tags.append(raw)
                    text = clean_text(raw)
                    if text:
                        start, end = meta[index]
                        segments.append(Segment(start=start, end=end, text=text))

                batch, meta, batch_seconds = [], [], 0.0

            for start, end in ranges:
                length = end - start
                if length <= _MIN_SEGMENT_SECONDS:
                    continue
                handle.seek(int(start * rate))
                data = handle.read(int(length * rate), dtype="float32", always_2d=True)
                if data.size == 0:
                    continue
                batch.append(data.mean(axis=1))
                meta.append((start, end))
                batch_seconds += length
                if batch_seconds >= _MAX_BATCH_SECONDS or len(batch) >= _MAX_BATCH_ITEMS:
                    flush()
            flush()

        return segments, "".join(tags)

    def _transcribe_whole(self, model, wav_path: Path, language: str | None) -> tuple[list[Segment], str]:
        try:
            results = model.generate(
                input=str(wav_path),
                cache={},
                language=language or "auto",
                use_itn=True,
                batch_size_s=self.batch_size_s,
            )
        except Exception as exc:
            raise RuntimeError(f"SenseVoice 推理失败: {exc}") from exc

        if isinstance(results, dict):
            results = [results]

        segments: list[Segment] = []
        tags: list[str] = []
        for item in results or []:
            if not isinstance(item, dict):
                continue
            raw = str(item.get("text") or "")
            if raw:
                tags.append(raw)
            text = clean_text(raw)
            if text:
                segments.append(Segment(start=0.0, end=0.0, text=text))
        return segments, "".join(tags)
