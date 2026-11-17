"""安装与启动契约测试。

目标：Windows 11 上两条命令即可完成安装与启动，不依赖手工安装 PyTorch：
    python -m pip install -r requirements.txt
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_install_torch_cpu_together():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in text
    assert "torch>=" in text
    assert "torchaudio>=" in text
    assert "uvicorn" in text


def test_lock_file_is_self_contained():
    text = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8")
    assert text.lstrip("\ufeff").startswith("--extra-index-url https://download.pytorch.org/whl/cpu")
    assert "torch==" in text
    assert "+cpu" in text
