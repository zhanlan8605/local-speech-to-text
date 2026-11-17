"""预下载 ASR 相关模型到项目 models/ 目录。

用法：
    python scripts/download_models.py            # 下载 ASR + VAD
    python scripts/download_models.py --check    # 只检查本地是否已存在

模型默认缓存位置由 app/config.py 中的 MODELSCOPE_CACHE 决定（项目内 models/）。
下载完成后可设置 A2T_OFFLINE=1，让服务完全离线运行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import MODELS_DIR, settings  # noqa: E402


def _snapshot_download(model_id: str) -> str:
    try:
        from modelscope.hub.snapshot_download import snapshot_download
    except ImportError:  # 兼容旧版 modelscope
        from modelscope import snapshot_download  # type: ignore
    return snapshot_download(model_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载本地语音识别模型")
    parser.add_argument("--check", action="store_true", help="仅检查模型是否已存在")
    args = parser.parse_args()

    model_ids = [settings.asr_model]
    if settings.vad_model:
        model_ids.append(settings.vad_model)
    if settings.punc_model:
        model_ids.append(settings.punc_model)

    print(f"模型缓存目录：{MODELS_DIR}")
    failed = 0
    for model_id in model_ids:
        try:
            print(f"-> {model_id}")
            local_path = _snapshot_download(model_id)
            print(f"   {local_path}")
        except Exception as exc:  # 网络问题或模型 id 变更
            failed += 1
            print(f"   失败：{exc}", file=sys.stderr)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
