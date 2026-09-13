"""Stage: (queue top-up) -> idea

Generates topic ideas from three complementary signals, then synthesises
them with Gemini into concrete, specific topics ready to script:

  1. ENGAGEMENT ANALYSIS — reads `reach` and `saves` from already-published
     rows in the sheet to compute per-pillar save-rates and surface the
     highest-performing topics. Save rate (saves / reach) is the single
     strongest proxy for algorithm-friendliness on Instagram: content people
     save is content the algorithm re-distributes.

  2. TREND SIGNAL — calls `pipeline/trend_research.py`, which:
       • Uses Gemini with Google Search grounding to summarise what is
         currently performing on Instagram, TikTok, Pinterest, and YouTube
         in the account's content niche (no extra API key required).
       • Optionally queries the YouTube Data API v3 for recent high-view
         short videos per pillar (requires YOUTUBE_API_KEY in env).

  3. TOPIC BANK — `topic_bank.json` provides the content pillars and a
     seasonal calendar. This is the stability layer: it ensures long-term
     topical coverage even when the other signals have nothing new.

Gemini synthesises all three into `count` ideas. If the combined signal
suggests an underserved niche not covered by the existing pillars, Gemini
can propose up to 2 new ones — the module writes them to `topic_bank.json`
automatically so the next run picks them up with no human action needed.
"""
import json
import os
from datetime import date

import config
from gemini_helper import generate_content as _gemini_generate
from pipeline import avatar_manager, trend_research

_TOPIC_BANK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "topic_bank.json"
)


# ---------------------------------------------------------------------------
# Signal 1: Engagement analysis from own published content
# ---------------------------------------------------------------------------

def _safe_int(value) -> int:
    try:
        return max(0, int(str(value).strip()))
    except (ValueError, TypeError):
        return 0


def _analyze_engagement(all_rows: list[dict]) -> dict:
    """Computes per-pillar engagement stats from published rows that have
    both `reach` and `saves` filled in. Returns:
      pillar_stats: {pillar: {post_count, avg_reach, avg_saves, save_rate_pct}}
      top_topics:   [(topic, save_rate_pct), ...] sorted best-first
    """
    pillar_acc: dict[str, dict] = {}
    topic_rows: list[tuple[str, int, float]] = []  # (topic, reach, save_rate)

    for row in all_rows:
        if row.get("status") != "published":
            continue
        reach = _safe_int(row.get("reach", ""))
        saves = _safe_int(row.get("saves", ""))
        if reach == 0:
            continue

        pillar = (row.get("pillar") or "unknown").strip()
        if pillar not in pillar_acc:
            pillar_acc[pillar] = {"reach": 0, "saves": 0, "count": 0}
        pillar_acc[pillar]["reach"] += reach
        pillar_acc[pillar]["saves"] += saves
        pillar_acc[pillar]["count"] += 1

        topic = (row.get("topic") or "").strip()
        if topic:
            topic_rows.append((topic, reach, saves / reach))

    pillar_stats: dict[str, dict] = {}
    for pillar, d in pillar_acc.items():
        pillar_stats[pillar] = {
            "post_count": d["count"],
            "avg_reach": round(d["reach"] / d["count"]),
            "avg_saves": round(d["saves"] / d["count"]),
            "save_rate_pct": round(d["saves"] / d["reach"] * 100, 2),
        }

    # Top topics by save rate — require ≥200 reach to filter noise
    top_topics = sorted(
        [(t, r, sr) for t, r, sr in topic_rows if r >= 200],
        key=lambda x: x[2],
        reverse=True,
    )[:8]

    return {
        "pillar_stats": pillar_stats,
        "top_topics": [(t, round(sr * 100, 2)) for t, _, sr in top_topics],
    }


def _format_engagement_section(stats: dict) -> str:
    if not stats["pillar_stats"] and not stats["top_topics"]:
        return ""

    lines = ["OWN ACCOUNT ENGAGEMENT DATA (published posts):"]

    if stats["pillar_stats"]:
        lines.append(
            "Per-pillar stats — save rate = saves÷reach, "
            "the primary algorithm-resonance signal:"
        )
        for pillar, s in sorted(
            stats["pillar_stats"].items(),
            key=lambda x: x[1]["save_rate_pct"],
            reverse=True,
        ):
            lines.append(
                f"  {pillar}: {s['post_count']} posts | "
                f"avg reach {s['avg_reach']:,} | "
                f"avg saves {s['avg_saves']:,} | "
                f"save rate {s['save_rate_pct']}%"
            )
        lines.append(
            "(High save rate + high reach = algorithm favourite. "
            "High save rate + low reach = niche but useful — worth expanding. "
            "Low save rate = audience not finding it bookmark-worthy.)"
        )

    if stats["top_topics"]:
        lines.append("Best individual topics by save rate:")
        for topic, sr in stats["top_topics"]:
            lines.append(f"  - {topic}  [{sr}% save rate]")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# topic_bank.json helpers
# ---------------------------------------------------------------------------

def _load_topic_bank() -> dict:
    with open(_TOPIC_BANK_PATH) as f:
        return json.load(f)


def _maybe_add_new_pillar(name: str) -> bool:
    """Appends a new pillar to topic_bank.json if it is not already there.
    Returns True if the file was actually modified.
    """
    bank = _load_topic_bank()
    existing_lower = {p.lower() for p in bank["pillars"]}
    if name.lower() in existing_lower:
        return False
    bank["pillars"].append(name)
    with open(_TOPIC_BANK_PATH, "w") as f:
        json.dump(bank, f, indent=2)
    print(f"  New pillar added to topic_bank.json: '{name}'")
    return True


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT = """\
You generate topic ideas for an AI-curated Instagram account.
Today: {today}  |  Seasonal angle this month: {seasonal_theme}

EXISTING CONTENT PILLARS (use these as the primary pillar labels):
{pillar_list}

{engagement_section}

{trend_section}

TOPICS ALREADY COVERED — skip these and close variants:
{recent_topics}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TASK: Generate exactly {count} new, specific, non-overlapping ideas.

Each idea must be concrete enough to script a single 30-45 second Reel
(not a broad theme — e.g. "5 under-desk cable management hacks" not "cable
management").

Decision logic:
• Topics with high save rate in our own data are algorithm-proven — generate
  more content in those angles and pillar directions.
• Topics with high reach but low save rate need a fresh hook — try a more
  bookmark-worthy angle before abandoning the pillar.
• Use the trend signal to surface new angles inside proven pillars, and to
  spot emerging sub-niches the account isn't covering yet.
• Balance: ~60 % towards pillars with strong engagement evidence, ~40 %
  exploratory (new angles, seasonal themes, trend-driven topics).

Return one idea per line, exactly as:
pillar | topic

No numbering, no extra text, no blank lines between ideas.

OPTIONAL — EMERGING PILLARS:
If the trend or engagement signal reveals a clearly underserved niche that
doesn't fit any existing pillar, propose up to 2 new ones AFTER the idea
list. Only suggest a new pillar if there is real signal for it. Format:
NEW_PILLAR: <pillar name>
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_ideas(
    count: int,
    recent_topics: list[str],
    all_rows: list[dict] | None = None,
) -> list[dict]:
    """Returns up to `count` new {"pillar": ..., "topic": ...} dicts.

    `recent_topics` — topic strings from the last ~50 sheet rows.
    `all_rows`      — all sheet rows; used for engagement analysis when
                      published rows with metrics are present.
    """
    bank = _load_topic_bank()
    month_name = date.today().strftime("%B")
    seasonal_theme = bank["seasonal_calendar"].get(
        month_name, "none — evergreen only"
    )
    pillar_list = ", ".join(bank["pillars"])

    # Signal 1: own account engagement
    engagement_stats = _analyze_engagement(all_rows or [])
    engagement_section = _format_engagement_section(engagement_stats)
    if not engagement_section:
        engagement_section = (
            "OWN ACCOUNT ENGAGEMENT DATA: none yet "
            "(no published posts with metrics in the sheet)."
        )

    # Signal 2: live trend research (Gemini grounding + optional YouTube)
    print("  Fetching trend signal for idea generation...")
    trends = trend_research.fetch_trends(pillar_list)

    trend_parts: list[str] = []
    if trends["web_signal"]:
        trend_parts.append(
            "CURRENT TREND SIGNAL (live web research across Instagram,"
            " TikTok, Pinterest, YouTube):\n" + trends["web_signal"]
        )
    if trends["youtube_signal"]:
        yt_block = "\n".join(f"- {t}" for t in trends["youtube_signal"])
        trend_parts.append(
            "RECENTLY HIGH-VIEW SHORT VIDEOS ON YOUTUBE"
            " (directional inspiration — reinterpret, do not copy):\n"
            + yt_block
        )
    trend_section = (
        "\n\n".join(trend_parts)
        if trend_parts
        else (
            "TREND SIGNAL: unavailable this run — "
            "rely on engagement data and topic bank."
        )
    )

    prompt = _PROMPT.format(
        today=date.today().isoformat(),
        seasonal_theme=seasonal_theme,
        pillar_list=pillar_list,
        engagement_section=engagement_section,
        trend_section=trend_section,
        recent_topics=(
            "\n".join(f"- {t}" for t in recent_topics) or "(none yet)"
        ),
        count=count,
    )

    text = _gemini_generate(prompt)

    ideas: list[dict] = []
    new_pillars: list[str] = []

    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("NEW_PILLAR:"):
            name = line.split(":", 1)[1].strip()
            if name:
                new_pillars.append(name)
        elif "|" in line:
            pillar, topic = line.split("|", 1)
            pillar, topic = pillar.strip(), topic.strip()
            if pillar and topic:
                ideas.append({"pillar": pillar, "topic": topic})

    # Write any new pillars to topic_bank.json (capped at 2 per run)
    for name in new_pillars[:2]:
        _maybe_add_new_pillar(name)

    # Avatar evolution: idea generation already has engagement stats and trend
    # signal in scope, making it the natural place to check for warranted
    # style/personality updates. Evolution is conservative and gated by
    # cooldown — this call is fast when no pillar qualifies.
    avatar_manager.maybe_evolve_avatars(
        all_rows=all_rows or [],
        engagement_stats=engagement_stats,
        trend_signal=trends.get("web_signal", ""),
    )

    return ideas[:count]
