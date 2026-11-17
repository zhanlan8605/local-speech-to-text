"""API 请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="ok 表示模型已就绪，warming 表示仍在加载")
    model_loaded: bool
    device: str
    backend: str
    threads: int
    max_upload_mb: int
    version: str


class SegmentModel(BaseModel):
    start: float
    end: float
    text: str


class TranscribeResponse(BaseModel):
    text: str
    language: str
    duration_s: float
    elapsed_s: float
    model: str
    segments: list[SegmentModel] = Field(default_factory=list)
