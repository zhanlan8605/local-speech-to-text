"""集中配置。

所有可调项都能用环境变量覆盖，方便在不同机器上复现同一套行为。
本模块必须在导入 funasr / modelscope 之前被导入，否则模型缓存目录不会生效。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
BASE_DIR = APP_DIR.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = BASE_DIR / "samples"
MODELS_DIR = Path(os.getenv("A2T_MODELS_DIR", str(BASE_DIR / "models")))

# modelscope 通过该环境变量决定模型缓存位置，必须在 import modelscope 前设置
os.environ.setdefault("MODELSCOPE_CACHE", str(MODELS_DIR))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

if os.getenv("A2T_OFFLINE", "0") == "1":
    # 断网部署：模型必须已经下载到本地
    os.environ.setdefault("MODELSCOPE_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_threads() -> int:
    """默认线程数：不超过 8，避免在低核机器上过度订阅。"""
    cpu = os.cpu_count() or 4
    return max(1, min(8, cpu))


# FunASR 的短别名 → ModelScope 完整模型 id（用于定位本地缓存目录）
_MODEL_ALIASES = {
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
}


def resolve_local_model(model_id: str, models_dir: Path | None = None) -> str:
    """把模型 id 解析成本地目录，让已下载的模型完全离线加载。

    支持三种情况，都找不到时原样返回模型 id（由 FunASR 触发下载）：
    1. 传入的是已经存在的本地目录；
    2. ModelScope 缓存布局 `<models_dir>/models/<org>--<name>/snapshots/<version>`；
    3. 手工放在 `<models_dir>/<org>/<name>` 下的模型。
    """
    raw = str(model_id or "").strip()
    if not raw:
        return raw

    direct = Path(raw).expanduser()
    if direct.is_dir():
        return str(direct)

    raw = _MODEL_ALIASES.get(raw, raw)
    root = models_dir or MODELS_DIR

    normalized = raw.rstrip("/")
    if "/" in normalized:
        org, name = normalized.split("/", 1)
        base = root / "models" / f"{org}--{name}"
        snapshots = base / "snapshots"
        if snapshots.is_dir():
            candidates = [path for path in snapshots.iterdir() if path.is_dir()]
            if candidates:
                latest = max(candidates, key=lambda path: path.stat().st_mtime)
                return str(latest)
        if base.is_dir():
            return str(base)

    manual = root / normalized
    if manual.is_dir():
        return str(manual)
    return raw


@dataclass
class Settings:
    app_name: str = "Local Speech to Text"
    version: str = "0.1.0"

    # 推理设备：cpu | cuda（有 GPU 时设为 cuda 即可，无代码分叉）
    device: str = field(default_factory=lambda: os.getenv("A2T_DEVICE", "cpu"))
    threads: int = field(default_factory=lambda: _env_int("A2T_THREADS", _default_threads()))

    max_upload_mb: int = field(default_factory=lambda: _env_int("A2T_MAX_UPLOAD_MB", 10))

    # 实时识别（WebSocket）：单次最长时长，设为 0 可关闭该功能
    stream_max_seconds: int = field(
        default_factory=lambda: _env_int("A2T_STREAM_MAX_SECONDS", 60)
    )

    # 模型 id（ModelScope 命名空间）
    asr_model: str = field(default_factory=lambda: os.getenv("A2T_ASR_MODEL", "iic/SenseVoiceSmall"))
    vad_model: str = field(
        default_factory=lambda: os.getenv(
            "A2T_VAD_MODEL", "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
        )
    )
    # 留空表示不启用标点模型；Phase 2 可设为 "ct-punc"
    punc_model: str = field(default_factory=lambda: os.getenv("A2T_PUNC_MODEL", ""))

    backend: str = "funasr"

    allowed_languages: tuple[str, ...] = ("auto", "zh", "en", "ja", "ko", "yue")
    allowed_formats: tuple[str, ...] = ("json", "txt", "srt")
    allowed_extensions: tuple[str, ...] = (
        ".wav",
        ".mp3",
        ".m4a",
        ".flac",
        ".ogg",
        ".opus",
        ".aac",
        ".wma",
        ".amr",
        ".mp4",
        ".mkv",
        ".mov",
        ".webm",
        ".avi",
    )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def streaming_enabled(self) -> bool:
        return self.stream_max_seconds > 0


settings = Settings()
