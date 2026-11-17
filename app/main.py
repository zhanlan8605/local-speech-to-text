"""应用入口。

运行（在项目根目录）：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .asr.base import ASREngine
from .asr.sensevoice import SenseVoiceEngine
from .config import STATIC_DIR, settings

logger = logging.getLogger(__name__)

# multipart 边界、字段头等额外开销的宽裕余量
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def build_engine() -> SenseVoiceEngine:
    return SenseVoiceEngine(
        model_id=settings.asr_model,
        vad_model_id=settings.vad_model or None,
        device=settings.device,
        threads=settings.threads,
        punc_model_id=settings.punc_model or None,
    )


def create_app(
    engine: ASREngine | None = None,
    start_warmup: bool = True,
) -> FastAPI:
    """工厂函数：测试可以注入假引擎并关闭预热。"""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = engine or build_engine()
        app.state.engine = active
        if start_warmup and engine is None:
            # 后台预热，避免阻塞启动；/health 会返回 warming
            threading.Thread(
                target=active.warmup, name="model-warmup", daemon=True
            ).start()
        try:
            yield
        finally:
            active.unload()

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="本地离线语音转文字服务（SenseVoice / FunASR）",
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(router)

    @app.middleware("http")
    async def reject_oversized_upload(request: Request, call_next):
        """在 multipart 解析前按 Content-Length 预判，避免超大文件先落盘。"""
        if request.method == "POST" and request.url.path == "/api/transcribe":
            raw = request.headers.get("content-length", "")
            if raw.isdigit():
                limit = settings.max_upload_bytes + _MULTIPART_OVERHEAD_BYTES
                if int(raw) > limit:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"文件超过 {settings.max_upload_mb}MB 上限"},
                    )
        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


_configure_logging()

app = create_app()
