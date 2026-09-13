"""Loads configuration from environment variables (.env locally, or repo
secrets when run inside GitHub Actions). Every other module imports from here
instead of calling os.environ directly, so there's one place to see every
setting the pipeline depends on.
"""

import os
import re
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _extract_sheet_id(value: str) -> str:
    """Accept a bare Sheet ID, a full Sheets URL, or an ID with a trailing
    /edit path (i.e. whatever the user copied from the browser address bar).
    """
    # Full URL: https://docs.google.com/spreadsheets/d/<ID>/edit?...
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    # ID pasted with trailing /edit?gid=0#gid=0 but without the domain
    return value.split("/")[0]


GOOGLE_SERVICE_ACCOUNT_FILE = _require("GOOGLE_SERVICE_ACCOUNT_FILE")
SHEET_ID = _extract_sheet_id(_require("SHEET_ID"))
SHEET_TAB_NAME = os.environ.get("SHEET_TAB_NAME", "Queue")

GEMINI_API_KEY = _require("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

IG_BUSINESS_ACCOUNT_ID = _require("IG_BUSINESS_ACCOUNT_ID")
IG_ACCESS_TOKEN = _require("IG_ACCESS_TOKEN")
IG_GRAPH_API_VERSION = "v21.0"
# Instagram Login tokens (IGAG prefix) use graph.instagram.com,
# not graph.facebook.com
IG_GRAPH_API_BASE = "https://graph.instagram.com"

# Optional — YouTube Data API v3 key for trending-video signal in idea
# generation. Enable "YouTube Data API v3" in the same Google Cloud project,
# create an API key restricted to that API. 10 000 quota units/day free;
# this pipeline uses ~100 units per content pillar per run.
# Leave blank and the YouTube signal is silently skipped.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# Optional — Reddit research signal. Leave blank and the pipeline skips it
# cleanly. See pipeline/reddit_research.py for the ToS scope this is limited to.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "ig-ai-pipeline-research/0.1")
REDDIT_SUBREDDITS = [
    s.strip() for s in os.environ.get("REDDIT_SUBREDDITS", "").split(",") if s.strip()
]

DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "")

# GitHub repo (owner/repo) and token for uploading assembled videos to a
# Release asset, giving Instagram a stable public URL to fetch.
# In GitHub Actions this is secrets.GITHUB_TOKEN (no extra setup needed).
# Locally, create a PAT with `repo` scope at GitHub → Settings → Developer settings.
GH_REPO = _require("GH_REPO")
GH_TOKEN = _require("GH_TOKEN")
AI_DISCLOSURE_TEXT = os.environ.get(
    "AI_DISCLOSURE_TEXT", "This account uses AI-generated content."
)

# The sheet's column layout — change here if you rename/reorder columns,
# nowhere else.
COLUMNS = [
    "id",
    "status",
    "pillar",
    "topic",
    "caption",
    "script",
    "asset_type",         # "image" or "video" — user fills in
    "asset_prompt",       # AI-generated prompt for the chosen tool — auto-filled
    "images_folder_id",
    "voice_path",
    "video_path",
    "affiliate_link",
    "scheduled_date",
    "qa_notes",
    "media_id",
    "published_at",
    "reach",
    "saves",
]

# How many un-published rows to keep queued up at all times. When the count
# drops below this, generate_ideas.py tops it back up automatically.
IDEA_BUFFER_SIZE = int(os.environ.get("IDEA_BUFFER_SIZE", "10"))

# Number of images generated per row by Pollinations.AI (free, no API key).
# More images = longer slideshow variety; 4 is a good default for a ~30s reel.
IMAGE_COUNT = int(os.environ.get("IMAGE_COUNT", "4"))
