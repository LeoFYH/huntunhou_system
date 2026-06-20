from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

WEB_DIR = ROOT_DIR / "web"
STORAGE_DIR = Path(os.getenv("HUNTUNHOU_STORAGE", ROOT_DIR / "storage")).resolve()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

ROBOT_API_BASE = os.getenv("ROBOT_API_BASE", "").rstrip("/")
ROBOT_API_TIMEOUT_SECONDS = float(os.getenv("ROBOT_API_TIMEOUT_SECONDS", "20"))
