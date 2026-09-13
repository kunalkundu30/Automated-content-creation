"""Per-pillar avatar management: visual identity, personality, and evolution.

Each content pillar has one avatar — a realistic human character who appears
as the consistent subject in every image and video generated for that pillar.
The avatar locks in physical traits that must stay identical across all assets
(age, build, skin tone, hair, distinguishing features) while allowing
context-appropriate clothing to vary per scene.

LIFECYCLE
---------
Creation:  The first time a pillar needs an image/video prompt, `avatar_manager`
           generates a detailed character definition using Gemini. This happens
           lazily in `_handle_drafted()` inside `main.py`.

Embedding: `build_visual_anchor(avatar)` returns a compact description string
           that is injected into both the Gemini-generated asset prompt
           (so the LLM frames the scene around the character) and the
           Pollinations.AI image URL (as a hard visual consistency constraint).

Evolution: After each idea-generation cycle, `maybe_evolve_avatars()` checks
           whether any pillar has enough engagement data (≥5 published posts)
           and sufficient cooldown (≥30 days since last change). If so, Gemini
           proposes a conservative style update based on save-rate patterns and
           live trend signal. Immutable traits (name, age, skin tone, build)
           are enforced in code regardless of the model response.

STORAGE
-------
Definitions are stored in `avatar_bank.json` in the pipeline root alongside
`topic_bank.json`. The file is created on first write and committed to the repo
so the same characters persist across GitHub Actions runs.
"""
import json
import os
import re
from datetime import date, timedelta

import config
from gemini_helper import generate_content as _gemini_generate
_AVATAR_BANK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "avatar_bank.json"
)

# Evolution is conservative: minimum posts before considering a change,
# and minimum days since the last change.
_MIN_POSTS_FOR_EVOLUTION = 5
_EVOLUTION_COOLDOWN_DAYS = 30
_MAX_EVOLUTIONS_PER_RUN = 2


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _load() -> dict:
    if not os.path.exists(_AVATAR_BANK_PATH):
        return {}
    with open(_AVATAR_BANK_PATH) as f:
        return json.load(f)


def _save(bank: dict) -> None:
    with open(_AVATAR_BANK_PATH, "w") as f:
        json.dump(bank, f, indent=2)


def _key(pillar: str) -> str:
    """Normalises a pillar name to its storage key."""
    return pillar.strip().lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_avatar(pillar: str) -> dict | None:
    """Returns the avatar dict for this pillar, or None if not yet created."""
    return _load().get(_key(pillar))


def get_or_create_avatar(pillar: str) -> dict:
    """Returns the existing avatar for this pillar, or generates a new one
    with Gemini and saves it to avatar_bank.json.
    """
    bank = _load()
    k = _key(pillar)
    if k in bank:
        return bank[k]

    print(f"  Avatar: no avatar for '{pillar}' — generating with Gemini...")
    avatar = _generate_avatar(pillar)
    bank[k] = avatar
    _save(bank)
    print(f"  Avatar: '{avatar['name']}' created for pillar '{pillar}'.")
    return avatar


def maybe_evolve_avatars(
    all_rows: list[dict],
    engagement_stats: dict,
    trend_signal: str,
) -> None:
    """Checks each pillar's avatar against evolution criteria and applies
    conservative updates when warranted. At most _MAX_EVOLUTIONS_PER_RUN
    pillars are updated per pipeline run so the process stays fast.

    Evolution criteria (ALL must be met):
      - ≥ _MIN_POSTS_FOR_EVOLUTION published posts for that pillar
      - ≥ _EVOLUTION_COOLDOWN_DAYS since the last evolution (or creation)
    """
    bank = _load()
    evolved = 0

    for pillar_key, avatar in bank.items():
        if evolved >= _MAX_EVOLUTIONS_PER_RUN:
            break

        # Gate 1: enough published posts for this pillar
        published_count = sum(
            1 for r in all_rows
            if r.get("status") == "published"
            and _key(r.get("pillar", "")) == pillar_key
        )
        if published_count < _MIN_POSTS_FOR_EVOLUTION:
            continue

        # Gate 2: cooldown since last evolution
        anchor_date = avatar.get("last_evolved") or avatar.get("created_at", "")
        if anchor_date:
            try:
                days_since = (
                    date.today() - date.fromisoformat(anchor_date)
                ).days
                if days_since < _EVOLUTION_COOLDOWN_DAYS:
                    continue
            except ValueError:
                pass  # Malformed date — allow evolution

        # Build per-pillar context for the evolution prompt
        pillar_stats = (
            engagement_stats.get("pillar_stats", {}).get(pillar_key, {})
        )
        top_topics_for_pillar = [
            topic
            for topic, _ in engagement_stats.get("top_topics", [])
            # top_topics is account-wide; pass all of them as context
        ]

        print(
            f"  Avatar: evaluating evolution for '{pillar_key}' "
            f"({published_count} published posts)..."
        )
        updated = _try_evolve(
            avatar, pillar_stats, top_topics_for_pillar, trend_signal
        )
        if updated:
            bank[pillar_key] = updated
            _save(bank)
            evolved += 1
            print(
                f"  Avatar: '{updated['name']}' evolved for '{pillar_key}'. "
                f"Change: {updated['evolution_log'][-1].get('change', '—')}"
            )


def build_visual_anchor(avatar: dict) -> str:
    """Returns the compact visual-consistency string injected into image and
    video generation prompts so every asset shows the same person.

    Format is tuned for Flux (Pollinations.AI) and Gemini asset prompts:
    concrete physical descriptors first, then style guidance.
    """
    v = avatar.get("visual", {})
    parts = [
        f"{avatar.get('name', 'the subject')},",
        f"{v.get('age_range', '')},",
        f"{v.get('build', '')},",
        f"{v.get('hair', '')} hair,",
        f"{v.get('skin_tone', '')} skin,",
        f"{v.get('distinguishing_features', '')}.",
        f"Style baseline: {v.get('style_baseline', '')}.",
        avatar.get("style_context_rules", ""),
    ]
    return " ".join(p for p in parts if p.strip(" .,"))


# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------

_CREATION_PROMPT = """\
Design a realistic, relatable Instagram content creator avatar for the
"{pillar}" content niche. This character will appear as the consistent human
subject in every image and short video produced for this pillar — the same
face and physical traits in every piece of content, with only clothing and
setting changing between posts.

Design criteria:
- Genuinely appeals to the target demographic of the "{pillar}" category
- Has a realistic appearance that AI image models (Flux, SDXL) can reproduce
  consistently from a text description alone
- Feels authentic and aspirational for lifestyle/home short-form content
- Warm, trustworthy personality suited to educational Instagram Reels
- IMPORTANT: The name must be different from all names already in use: {taken_names}

For the "voice" field, pick the Google Cloud TTS Journey voice that best fits
the avatar's nationality and content language. Choose F (female) or D (male)
to match the avatar's gender. Available voices:
  en-US → en-US-Journey-F / en-US-Journey-D  (American English)
  en-GB → en-GB-Journey-F / en-GB-Journey-D  (British English)
  en-AU → en-AU-Journey-F / en-AU-Journey-D  (Australian English)
  en-IN → en-IN-Journey-F / en-IN-Journey-D  (Indian English)
  fr-FR → fr-FR-Journey-F / fr-FR-Journey-D  (French)
  de-DE → de-DE-Journey-F / de-DE-Journey-D  (German)
  es-ES → es-ES-Journey-F / es-ES-Journey-D  (Spanish, Spain)
  es-US → es-US-Journey-F / es-US-Journey-D  (Spanish, Latin America)
  pt-BR → pt-BR-Journey-F / pt-BR-Journey-D  (Portuguese, Brazil)
  it-IT → it-IT-Journey-F / it-IT-Journey-D  (Italian)
  nl-NL → nl-NL-Journey-F / nl-NL-Journey-D  (Dutch)
  pl-PL → pl-PL-Journey-F / pl-PL-Journey-D  (Polish)
  hi-IN → hi-IN-Journey-F / hi-IN-Journey-D  (Hindi)
  ja-JP → ja-JP-Journey-F / ja-JP-Journey-D  (Japanese)
  ko-KR → ko-KR-Journey-F / ko-KR-Journey-D  (Korean)
  cmn-CN → cmn-CN-Journey-F / cmn-CN-Journey-D  (Mandarin Chinese)
  ar-XA → ar-XA-Journey-F / ar-XA-Journey-D  (Arabic)
If the avatar has a non-English background but targets an international
audience, keep content_language as "English" and pick an accented English
voice (e.g. en-IN, en-AU). Set content_language to a non-English value only
when the entire target audience reads that language exclusively.

Return ONLY a valid JSON object — no markdown fences, no commentary:

{{
  "name": "<a realistic given name only — must NOT be any of: {taken_names}>",
  "created_at": "{today}",
  "last_evolved": "{today}",
  "visual": {{
    "age_range": "<e.g. 'mid-20s'>",
    "build": "<brief e.g. 'average height, slim build'>",
    "hair": "<color and style e.g. 'warm chestnut, shoulder-length straight'>",
    "skin_tone": "<e.g. 'medium warm beige'>",
    "distinguishing_features": "<1-2 subtle repeatable traits e.g. 'small gold studs, natural brows'>",
    "style_baseline": "<default off-duty look e.g. 'neutral basics, linen tones, white sneakers'>"
  }},
  "personality": {{
    "core_traits": ["<trait1>", "<trait2>", "<trait3>"],
    "content_voice": "<one sentence — how they come across on camera>",
    "target_demographic": "<who this pillar's content is for>",
    "nationality": "<e.g. 'American', 'British', 'Australian', 'French', 'Indian', 'Brazilian'>"
  }},
  "voice": {{
    "language_code": "<BCP-47 code from the list above e.g. 'en-US'>",
    "voice_name": "<Journey voice name from the list above e.g. 'en-US-Journey-F'>",
    "speaking_rate": 1.05,
    "content_language": "<human-readable language name e.g. 'English', 'French', 'Spanish'>"
  }},
  "style_context_rules": "Clothing adapts to the content scene (e.g. cosy knitwear for winter content, gym-adjacent for productivity posts) while face, hair, and distinguishing features remain identical across every asset.",
  "evolution_log": [
    {{"date": "{today}", "change": "initial creation", "reason": "new pillar"}}
  ]
}}
"""

_EVOLUTION_PROMPT = """\
You are reviewing whether a subtle visual or personality update is warranted
for an Instagram content avatar, based on new engagement and trend data.

CURRENT AVATAR:
{avatar_json}

ENGAGEMENT DATA FOR THIS PILLAR:
{pillar_stats}

TOP-PERFORMING TOPICS (account-wide, for audience taste signal):
{top_topics}

CURRENT TREND SIGNAL:
{trend_signal}

RULES — strictly enforced in code regardless of your response:
  NEVER change: name, age_range, skin_tone, build.
  ONLY consider: hair style (never color), style_baseline, distinguishing_features
    (additive only, never remove), one core_trait swap, minor content_voice wording.
  Every change must be justified by a specific data point in the signals above.
  This is a subtle evolution, not a redesign.

If no data-supported change is warranted, respond with exactly: NO_CHANGE

If a small update is warranted, return the complete updated JSON (same schema)
with the evolution_log array extended by one entry: {{"date": "<today>",
"change": "<what changed>", "reason": "<which data point drove it>"}}.
Return only JSON — no markdown, no commentary.
"""


def _parse_json(text: str) -> dict | None:
    """Strips optional markdown fences and parses the first JSON object."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None


def _generate_avatar(pillar: str) -> dict:
    today = date.today().isoformat()
    bank = _load()
    taken = sorted({a["name"] for a in bank.values() if a.get("name")})
    taken_names = ", ".join(taken) if taken else "none"
    prompt = _CREATION_PROMPT.format(
        pillar=pillar, today=today, taken_names=taken_names
    )
    text = _gemini_generate(prompt)
    parsed = _parse_json(text)
    if not parsed:
        raise RuntimeError(
            f"Avatar creation for '{pillar}' returned unparseable JSON:\n{text}"
        )
    return parsed


def _try_evolve(
    avatar: dict,
    pillar_stats: dict,
    top_topics: list[str],
    trend_signal: str,
) -> dict | None:
    """Returns an updated avatar dict if evolution is warranted, else None.
    Immutable fields are re-applied from the original after parsing so the
    model cannot accidentally change them.
    """
    prompt = _EVOLUTION_PROMPT.format(
        avatar_json=json.dumps(avatar, indent=2),
        pillar_stats=(
            json.dumps(pillar_stats, indent=2) if pillar_stats else "no data"
        ),
        top_topics=(
            "\n".join(f"- {t}" for t in top_topics) or "none yet"
        ),
        trend_signal=trend_signal or "unavailable this run",
    )
    text = _gemini_generate(prompt).strip()

    if text.upper().startswith("NO_CHANGE"):
        return None

    parsed = _parse_json(text)
    if not parsed:
        return None

    # Enforce immutable fields regardless of model output
    parsed["name"] = avatar["name"]
    parsed["created_at"] = avatar["created_at"]
    parsed["last_evolved"] = date.today().isoformat()
    if "visual" in parsed and "visual" in avatar:
        for immutable in ("age_range", "skin_tone", "build"):
            parsed["visual"][immutable] = avatar["visual"][immutable]
    # Voice and language are immutable — changing them mid-series would break
    # audience consistency (a French-speaking avatar suddenly speaking Hindi).
    if "voice" in avatar:
        parsed["voice"] = avatar["voice"]

    return parsed
