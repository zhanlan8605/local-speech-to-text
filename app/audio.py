"""音频预处理：任意格式 -> 16kHz 单声道 PCM WAV。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .config import settings

# 压缩格式最小合理字节数，小于该值视为解码失败或空文件
_MIN_WAV_BYTES = 44


class AudioConversionError(RuntimeError):
    """ffmpeg 无法解码或转换输入音频。"""


def is_supported_filename(filename: str) -> bool:
    return Path(filename or "").suffix.lower() in settings.allowed_extensions


def ffmpeg_path() -> str:
    """优先使用 imageio-ffmpeg 自带的二进制，保证不依赖系统安装。"""
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:  # pragma: no cover - 正常安装不会走到这里
        return "ffmpeg"


def to_wav16k(src: Path, dst: Path, timeout: int = 600) -> Path:
    """把任意音频/视频转成 16kHz、单声道、16bit PCM 的 wav。"""
    cmd = [
        ffmpeg_path(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(dst),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except OSError as exc:  # ffmpeg 不存在或无法执行
        raise AudioConversionError(f"无法调用 ffmpeg: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioConversionError("音频转换超时") from exc

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size <= _MIN_WAV_BYTES:
        detail = [line for line in (proc.stderr or "").strip().splitlines() if line]
        message = detail[-1] if detail else "ffmpeg 转换失败"
        raise AudioConversionError(message)
    return dst


def wav_duration(path: Path) -> float:
    """读取 wav 时长（秒）；失败返回 0，不影响主流程。"""
    try:
        import soundfile as sf

        info = sf.info(str(path))
        if not info.samplerate:
            return 0.0
        return float(info.frames) / float(info.samplerate)
    except Exception:  # pragma: no cover - 仅在文件损坏时触发
        return 0.0
