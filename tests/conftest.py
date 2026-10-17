import os
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")

os.environ.setdefault("SERVER_LOCALHOST", "127.0.0.1")