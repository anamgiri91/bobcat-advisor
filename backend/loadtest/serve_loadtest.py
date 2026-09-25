"""
Serve the API for a local load test: SQLite database, sample catalog pages
and schedule from the test fixtures (no outbound requests), keyword-only
retrieval (no embedding model download), no LLM key, rate limit lifted.

  python loadtest/serve_loadtest.py          # then run locust against :8000
"""

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
work = Path(tempfile.mkdtemp(prefix="bobcat-load-"))
os.environ.update({
    "DATABASE_URL": f"sqlite:///{work / 'load.db'}", "GEMINI_API_KEY": "", "GROQ_API_KEY": "",
    "RETRIEVAL_MODE": "bm25", "RATE_LIMIT_PER_MINUTE": "1000000", "TIMETABLE_SOLVER": "search",
    "SECRETS_DIR": str(work), "ENVIRONMENT": "loadtest",
})
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

import uvicorn  # noqa: E402

from app.advising import web  # noqa: E402
from app.advising.web import parse_html  # noqa: E402
from app.structured import schedule  # noqa: E402
from tests.schedule_fixtures import FALL_2026_HTML  # noqa: E402
from tests.txst_fixtures import FakeFetcher  # noqa: E402

web.set_fetcher(FakeFetcher())
schedule.store_path = lambda: work / "schedule_sections.jsonl"
schedule.save_sections(schedule.sections_from_page(parse_html(FALL_2026_HTML), "Fall 2026"))

from app.main import app  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="warning",
            workers=1)
