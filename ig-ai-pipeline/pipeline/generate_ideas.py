"""Stage: (queue top-up) -> idea

Fully automated topic generation. Worth being upfront about what this is
NOT: live trend-scraping. The two obvious free options for that turned out
not to hold up —

- pytrends (the standard Google Trends library) is unmaintained (archived
  April 2025) and throws rate-limit errors on the very first call for most
  people as of 2026 — not something to build a business process on.
- Reddit's API is free at a usable rate limit, but its terms explicitly
  restrict the free tier to non-commercial use. This account is commercial
  (affiliate links, sponsorships), so that tier isn't actually available to
  it — the real commercial tier is priced far beyond this budget.

Instead, this blends a maintained topic bank (topic_bank.json) with Gemini,
and explicitly avoids repeating recent topics. It trades real-time
trend-sensitivity for something that's actually free and won't break — you
can always drop a genuinely timely idea into the sheet manually alongside
whatever this generates.
"""
import json
import os
import re
import time
from datetime import date

from google import genai

import config
from pipeline import reddit_research

_client = genai.Client(api_key=config.GEMINI_API_KEY)


def _gemini_generate(prompt: str) -> str:
    """Calls Gemini and retries up to 5 times on 429 rate-limit errors,
    honouring the retry delay the API suggests.
    """
    for attempt in range(5):
        try:
            return _client.models.generate_content(
                model=config.GEMINI_MODEL, contents=prompt
            ).text
        except Exception as exc:
            msg = str(exc)
            if ("429" in msg or "RESOURCE_EXHAUSTED" in msg) and attempt < 4:
                match = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                wait = float(match.group(1)) + 2 if match else 15
                print(f"Rate limited — waiting {wait:.0f}s (attempt {attempt + 1}/5)...")
                time.sleep(wait)
            else:
                raise

_TOPIC_BANK_PATH = os.path.join(os.path.dirname(__file__), "..", "topic_bank.json")
with open(_TOPIC_BANK_PATH) as f:
    _TOPIC_BANK = json.load(f)

_PROMPT_TEMPLATE = """\
You generate topic ideas for an AI-curated Instagram account covering: {pillar_list}.

This month's seasonal angle to lean into where it fits naturally: {seasonal_theme}

Topics already covered recently — do not repeat these or close variants:
{recent_topics}
{reddit_section}
Generate exactly {count} new, specific, non-overlapping topic ideas. Each
should be concrete enough to write a single Reel about (not a broad theme).
For each, pick the single best-fitting pillar from: {pillar_list}.

Return one idea per line, formatted exactly as:
pillar | topic
No numbering, no extra commentary, no blank lines.
"""

_REDDIT_SECTION_TEMPLATE = """
Real discussion titles from the niche's community right now, for directional
inspiration only — do not copy these verbatim, reinterpret and generalize them:
{titles}
"""


def generate_ideas(count: int, recent_topics: list[str]) -> list[dict]:
    """Returns up to `count` new {"pillar": ..., "topic": ...} dicts.
    `recent_topics` should be the topic text from the last ~30-50 rows in
    the sheet, so Gemini has real context on what to avoid repeating.
    """
    month_name = date.today().strftime("%B")
    seasonal_theme = _TOPIC_BANK["seasonal_calendar"].get(month_name, "none — evergreen only")
    pillar_list = ", ".join(_TOPIC_BANK["pillars"])

    reddit_titles = reddit_research.fetch_signal_topics()
    reddit_section = (
        _REDDIT_SECTION_TEMPLATE.format(titles="\n".join(f"- {t}" for t in reddit_titles))
        if reddit_titles else ""
    )

    prompt = _PROMPT_TEMPLATE.format(
        pillar_list=pillar_list,
        seasonal_theme=seasonal_theme,
        recent_topics="\n".join(f"- {t}" for t in recent_topics) or "(none yet)",
        reddit_section=reddit_section,
        count=count,
    )
    text = _gemini_generate(prompt)

    ideas = []
    for line in text.strip().splitlines():
        if "|" not in line:
            continue
        pillar, topic = line.split("|", 1)
        pillar, topic = pillar.strip(), topic.strip()
        if pillar and topic:
            ideas.append({"pillar": pillar, "topic": topic})
    return ideas[:count]
