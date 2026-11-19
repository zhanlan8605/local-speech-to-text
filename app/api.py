"""HTTP 接口实现。

契约（v1）：
    GET  /health          服务健康检查
    POST /api/transcribe  上传音频并识别
    GET  /                 Web 页面（在 main.py 中注册）
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from .audio import AudioConversionError, is_supported_filename, to_wav16k, wav_duration
from .config import settings
from .schemas import HealthResponse, SegmentModel, TranscribeResponse
from .srt import to_srt

router = APIRouter()

_CHUNK_SIZE = 1024 * 1024  # 1MB


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    """始终快速返回，模型仍在加载时给出 warming 状态。"""
    engine = request.app.state.engine
    loaded = bool(getattr(engine, "loaded", False))
    return HealthResponse(
        status="ok" if loaded else "warming",
        model_loaded=loaded,
        device=settings.device,
        backend=settings.backend,
        threads=settings.threads,
        max_upload_mb=settings.max_upload_mb,
        streaming_enabled=settings.streaming_enabled,
        stream_max_seconds=max(0, settings.stream_max_seconds),
        version=settings.version,
    )


@router.post("/api/transcribe", tags=["asr"])
async def transcribe(
    request: Request,
    file: UploadFile = File(..., description="音频或视频文件"),
    language: str = Form("auto", description="auto | zh | en | ja | ko | yue"),
    response_format: str = Query("json", description="json | txt | srt"),
):
    """上传音频并同步返回识别结果。"""
    language = (language or "auto").strip().lower()
    if language not in settings.allowed_languages:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的语言：{language}，可选：{', '.join(settings.allowed_languages)}",
        )

    response_format = (response_format or "json").strip().lower()
    if response_format not in settings.allowed_formats:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的输出格式：{response_format}，可选：{', '.join(settings.allowed_formats)}",
        )

    filename = file.filename or ""
    if not is_supported_filename(filename):
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型：{filename or '(未知)'}，"
            f"支持：{', '.join(settings.allowed_extensions)}",
        )

    engine = request.app.state.engine

    with tempfile.TemporaryDirectory(prefix="a2t-") as tmpdir:
        tmp = Path(tmpdir)
        suffix = Path(filename).suffix.lower() or ".bin"
        raw_path = tmp / f"input{suffix}"
        size = await _save_upload(file, raw_path)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传的文件为空")

        wav_path = tmp / "audio.wav"
        try:
            await run_in_threadpool(to_wav16k, raw_path, wav_path)
        except AudioConversionError as exc:
            raise HTTPException(status_code=400, detail=f"音频解码失败：{exc}") from exc

        started = time.perf_counter()
        try:
            transcript = await run_in_threadpool(
                engine.transcribe, wav_path, None if language == "auto" else language
            )
        except Exception as exc:  # 模型未就绪、推理异常等
            raise HTTPException(status_code=500, detail=f"识别失败：{exc}") from exc
        elapsed = time.perf_counter() - started

        duration = transcript.duration_s or wav_duration(wav_path)

    if response_format == "txt":
        return PlainTextResponse(transcript.text, media_type="text/plain; charset=utf-8")

    if response_format == "srt":
        return PlainTextResponse(
            to_srt(transcript.segments, duration),
            media_type="application/x-subrip; charset=utf-8",
        )

    return TranscribeResponse(
        text=transcript.text,
        language=transcript.language,
        duration_s=round(duration, 3),
        elapsed_s=round(elapsed, 3),
        model=getattr(engine, "name", settings.backend),
        segments=[SegmentModel(start=s.start, end=s.end, text=s.text) for s in transcript.segments],
    )


async def _save_upload(upload: UploadFile, dst: Path) -> int:
    """分块落盘并强制大小上限，避免一次性读进内存。"""
    limit = settings.max_upload_bytes
    total = 0
    with dst.open("wb") as fh:
        while True:
            chunk = await upload.read(_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件超过 {settings.max_upload_mb}MB 上限",
                )
            fh.write(chunk)
    return total
