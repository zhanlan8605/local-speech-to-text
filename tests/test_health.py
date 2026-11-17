"""健康检查与首页测试。"""

from __future__ import annotations

from app.config import settings


def test_health_reports_ready_when_engine_loaded(client):
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["model_loaded"] is True
    assert {
        "status",
        "model_loaded",
        "device",
        "backend",
        "threads",
        "max_upload_mb",
        "version",
    } <= set(data)
    assert data["max_upload_mb"] == settings.max_upload_mb
    assert data["version"]


def test_health_reports_warming_when_engine_not_loaded():
    from fastapi.testclient import TestClient

    from app.asr.fake import FakeASREngine
    from app.main import create_app

    engine = FakeASREngine()
    engine._loaded = False

    app = create_app(engine=engine, start_warmup=False)
    with TestClient(app) as client:
        data = client.get("/health").json()

    assert data["status"] == "warming"
    assert data["model_loaded"] is False


def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "本地语音转文字" in response.text
