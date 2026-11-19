#!/usr/bin/env python
"""用真实模型验证 WebSocket 实时识别（需要先启动服务）。

用法（在项目根目录）：
    python scripts/check_stream.py                      # samples/chinese.wav，按 1 倍速发送
    python scripts/check_stream.py samples/english.wav  # 指定音频
    python scripts/check_stream.py --fast               # 不等实时节奏，尽快发完
    python scripts/check_stream.py --url ws://127.0.0.1:8000/ws/stream

输出会逐条打印 intermediate（partial）结果以及它相对开始说话的时间，
最后打印 final 文本，可直接用来观察端到端延迟与增量识别效果。

依赖：websockets（uvicorn[standard] 已包含）、numpy、soundfile
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
TARGET_RATE = 16000
FRAME_SAMPLES = 2048  # 与前端一致，约 128ms 一帧


def load_pcm(path: Path) -> bytes:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != TARGET_RATE:
        target = int(round(len(mono) * TARGET_RATE / rate))
        mono = np.interp(
            np.linspace(0, len(mono) - 1, target), np.arange(len(mono)), mono
        )
    return (np.clip(mono, -1, 1) * 32767).astype("<i2").tobytes()


async def run(url: str, pcm: bytes, fast: bool) -> int:
    try:
        import websockets
    except ImportError:  # pragma: no cover - 只在精简安装时触发
        print("缺少 websockets：python -m pip install websockets", file=sys.stderr)
        return 2

    frame_bytes = FRAME_SAMPLES * 2
    started = time.perf_counter()
    first_partial_at: float | None = None
    partials = 0

    async with websockets.connect(url, max_size=2**22) as ws:
        ready = json.loads(await ws.recv())
        print(f"connected: {ready}")

        await ws.send(json.dumps({"type": "start", "language": "auto"}))
        print(f"started:   {json.loads(await ws.recv())}")

        async def pump() -> None:
            sent = 0
            while sent < len(pcm):
                await ws.send(pcm[sent : sent + frame_bytes])
                sent += min(frame_bytes, len(pcm) - sent)
                if not fast:
                    delay = started + sent / 2 / TARGET_RATE - time.perf_counter()
                    if delay > 0:
                        await asyncio.sleep(delay)
            await ws.send(json.dumps({"type": "stop"}))
            print(f"[{time.perf_counter() - started:6.2f}s] 音频发送完毕")

        pump_task = asyncio.create_task(pump())
        final: dict | None = None
        try:
            while final is None:
                message = json.loads(await ws.recv())
                kind = message["type"]
                if kind == "partial":
                    partials += 1
                    if first_partial_at is None:
                        first_partial_at = time.perf_counter() - started
                    print(
                        f"[{time.perf_counter() - started:6.2f}s] partial "
                        f"audio={message['duration_s']:5.1f}s  {message['text']}"
                    )
                elif kind == "final":
                    final = message
                elif kind == "error":
                    print(f"error: {message['detail']}", file=sys.stderr)
                    return 1
        finally:
            pump_task.cancel()

    elapsed = time.perf_counter() - started
    assert final is not None
    print("\n--- final ---")
    print(f"时长: {final['duration_s']:.2f}s  解码累计: {final['elapsed_s']:.2f}s  "
          f"端到端: {elapsed:.2f}s  中间结果: {partials} 次")
    if first_partial_at is not None:
        print(f"首个中间结果: {first_partial_at:.2f}s 后出现")
    print(f"文本: {final['text']}")
    return 0 if final["text"].strip() else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="WebSocket 实时识别冒烟检查")
    parser.add_argument("audio", nargs="?", default=str(ROOT / "samples" / "chinese.wav"))
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/stream")
    parser.add_argument("--fast", action="store_true", help="不按实时速度发送")
    args = parser.parse_args()

    path = Path(args.audio)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        print(f"找不到音频文件：{path}", file=sys.stderr)
        return 2
    return asyncio.run(run(args.url, load_pcm(path), args.fast))


if __name__ == "__main__":
    raise SystemExit(main())
