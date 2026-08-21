"""
Load the root .env for local dev before any agent module reads os.environ.

This lives in the package __init__ so it happens exactly once and always
first — agents/claude.py reads ANTHROPIC_MODEL at import time, and the two
cron agents (python -m agents.saturday_suggest / .sunday_shopping) never
loaded a .env of their own. An explicit path rather than find_dotenv() so it
works regardless of the working directory (Railway runs from /app).

No-op in production, where the env vars are already set.
"""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
