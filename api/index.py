"""Vercel Python function: the whole hosted API (FastAPI) behind /api/*. See docs/OS.md."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from summer_hunter.os.hosted import build  # noqa: E402

app = build()
