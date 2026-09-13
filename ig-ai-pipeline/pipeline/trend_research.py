"""Fetches real-time trend signals from two complementary free sources:

  1. GEMINI WITH GOOGLE SEARCH GROUNDING — asks Gemini to run a live web
     search across Instagram, TikTok, Pinterest, and YouTube and return a
     concise summary of what is currently performing well in the content
     niche. No extra API key needed — uses the same GEMINI_API_KEY already
     configured, and Google Search grounding is included in Gemini's free
     tier. Falls back silently if the configured model doesn't support it.

  2. YOUTUBE DATA API v3 (optional) — searches for recently-uploaded,
     high-view-count short videos per pillar, returning video titles as a
     concrete signal of what's getting traction right now. Requires
     YOUTUBE_API_KEY in the environment (enable "YouTube Data API v3" in the
     same Google Cloud project; 10 000 quota units/day free — this module
     uses ~100 units per pillar per run). Leave YOUTUBE_API_KEY blank and
     this source is silently skipped.

Both sources degrade gracefully — the rest of the pipeline works fine with
an empty trend signal from here.
"""
from datetime import date, timedelta

import requests
from google import genai
from google.genai import types

import config

# Ordered list of fallback models tried when the primary hits a rate limit.
# Kept separate from config.GEMINI_MODEL so they have independent quota.
_GROUNDING_FALLBACKS = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
]
_GROUNDING_PROMPT = """\
You are a trend analyst for short-form social media content (Instagram Reels,
TikTok, Pinterest, YouTube Shorts). Today is {today}.

Search the web and summarize what is currently trending and performing well in
these content categories: {pillar_list}

I need actionable intelligence on:
1. Specific topic angles getting high engagement on Instagram Reels right now
2. Formats or hooks that are working well across TikTok and Pinterest
3. Underserved sub-niches or emerging themes within these categories
4. Any adjacent categories that appear to be growing in audience interest —
   even if they are not currently in the pillar list

Return a concise bullet-point summary of 10-15 specific, actionable insights.
Be concrete — name actual topics and angles, not vague trend labels.
"""

_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_RESULTS_PER_PILLAR = 6
_LOOKBACK_DAYS = 60


def fetch_trends(pillar_list: str) -> dict:
    """Returns {"web_signal": str, "youtube_signal": list[str]}.
    Both keys are always present; values are empty when a source is
    unavailable or fails.
    """
    web = _fetch_web_signal(pillar_list)
    yt = _fetch_youtube_signal(pillar_list)
    return {"web_signal": web, "youtube_signal": yt}


# ---------------------------------------------------------------------------
# Source 1: Gemini with Google Search grounding
# ---------------------------------------------------------------------------

def _fetch_web_signal(pillar_list: str) -> str:
    """Returns Gemini's grounded trend summary, or '' on any failure."""
    prompt = _GROUNDING_PROMPT.format(
        today=date.today().isoformat(),
        pillar_list=pillar_list,
    )
    # Try the configured model first (may support grounding), then fall back
    # to the known-good grounding model.
    for model in _candidate_models():
        result = _try_grounded_generate(model, prompt)
        if result is not None:
            return result

    print("  Trend research: web grounding unavailable for all tried models.")
    return ""


def _candidate_models() -> list[str]:
    # Primary model first, then fallbacks — deduplicated, order preserved
    seen: set[str] = set()
    out = []
    for m in [config.GEMINI_MODEL] + _GROUNDING_FALLBACKS:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _try_grounded_generate(model: str, prompt: str) -> str | None:
    """Attempts one grounded Gemini call. Returns text on success, None on
    any error (caller tries the next model).
    """
    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        search_tool = types.Tool(google_search=types.GoogleSearch())
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[search_tool]),
        )
        text = response.text
        if text and text.strip():
            print(f"  Trend research: web signal fetched via {model}.")
            return text.strip()
        return None
    except Exception as exc:
        print(f"  Trend research: {model} grounding failed — {exc}")
        return None


# ---------------------------------------------------------------------------
# Source 2: YouTube Data API v3 (optional)
# ---------------------------------------------------------------------------

def _fetch_youtube_signal(pillar_list: str) -> list[str]:
    """Returns video titles from recent high-view short videos, one search
    per pillar. Returns [] when YOUTUBE_API_KEY is not set or calls fail.
    """
    if not config.YOUTUBE_API_KEY:
        return []

    cutoff = (date.today() - timedelta(days=_LOOKBACK_DAYS)).strftime(
        "%Y-%m-%dT00:00:00Z"
    )
    titles: list[str] = []

    for pillar in pillar_list.split(","):
        pillar = pillar.strip()
        if not pillar:
            continue
        try:
            params = {
                "part": "snippet",
                "q": pillar,
                "type": "video",
                "videoDuration": "short",
                "order": "viewCount",
                "publishedAfter": cutoff,
                "maxResults": _RESULTS_PER_PILLAR,
                "key": config.YOUTUBE_API_KEY,
            }
            resp = requests.get(_YOUTUBE_SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            for item in resp.json().get("items", []):
                title = item.get("snippet", {}).get("title", "").strip()
                if title:
                    titles.append(title)
        except Exception as exc:
            print(f"  Trend research: YouTube signal for '{pillar}' failed — {exc}")

    if titles:
        print(f"  Trend research: {len(titles)} YouTube titles fetched.")
    return titles
