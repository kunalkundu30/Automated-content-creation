"""Shared Gemini API helper used by every pipeline stage that calls Gemini.

Handles three failure modes transparently:

  1. Per-minute rate limit (429, retryDelay in header)
     → Wait the suggested delay and retry the same model (up to 5 attempts).

  2. Daily free-tier quota exhausted (429, "free_tier_requests"/"PerDayPer")
     → Skip immediately to the next model in the fallback list.

  3. Model not found / deprecated (404 NOT_FOUND)
     → Skip immediately to the next model in the fallback list.

Model order: config.GEMINI_MODEL first, then _FALLBACK_MODELS (deduplicated).
Update _FALLBACK_MODELS here when Google deprecates or adds models.
"""
import re
import time

from google import genai

import config

_client = genai.Client(api_key=config.GEMINI_API_KEY)

# Fallback models tried in order when the primary is unavailable or over quota.
# gemini-2.5-flash and gemini-2.5-pro are listed as candidates; if Google has
# deprecated them the 404 handler skips them automatically so no manual
# cleanup is needed. Update this list as new models become available.
_FALLBACK_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.0-flash",
]


def _model_list() -> list[str]:
    """Returns the ordered list of models to try, deduped."""
    primary = config.GEMINI_MODEL
    seen = {primary}
    result = [primary]
    for m in _FALLBACK_MODELS:
        if m not in seen:
            result.append(m)
            seen.add(m)
    return result


def _is_daily_quota_error(msg: str) -> bool:
    """True when the 429 is a daily free-tier cap, not a per-minute limit."""
    return "free_tier_requests" in msg or "PerDayPer" in msg


def _is_model_not_found(msg: str) -> bool:
    """True when the model is deprecated or doesn't exist for this account."""
    return ("404" in msg or "NOT_FOUND" in msg) and (
        "no longer available" in msg
        or "not found" in msg.lower()
        or "not supported" in msg.lower()
    )


def generate_content(prompt: str) -> str:
    """Calls Gemini with automatic model fallback and rate-limit retry.

    Returns the model's text response. Raises RuntimeError only when every
    model in the list has been exhausted (daily quota or all deprecated).
    """
    last_exc: Exception | None = None

    for model in _model_list():
        for attempt in range(5):
            try:
                return _client.models.generate_content(
                    model=model, contents=prompt
                ).text
            except Exception as exc:
                msg = str(exc)
                last_exc = exc

                # Model deprecated / not available — skip to next without retry
                if _is_model_not_found(msg):
                    print(f"  {model} not available — skipping to next model...")
                    break

                # Not a quota/rate-limit error — propagate immediately
                if "429" not in msg and "RESOURCE_EXHAUSTED" not in msg:
                    raise

                # Daily free-tier quota exhausted — skip to next model
                if _is_daily_quota_error(msg):
                    print(
                        f"  Daily free-tier quota for {model} exhausted — "
                        "switching to next model..."
                    )
                    break

                # Per-minute rate limit — wait and retry same model
                if attempt < 4:
                    match = re.search(r"retry in (\d+(?:\.\d+)?)s", msg)
                    wait = float(match.group(1)) + 2 if match else 15
                    print(
                        f"Rate limited — waiting {wait:.0f}s "
                        f"(attempt {attempt + 1}/5)..."
                    )
                    time.sleep(wait)
                else:
                    print(
                        f"  {model}: 5 rate-limit retries exhausted — "
                        "switching to next model..."
                    )
                    break

    raise RuntimeError(
        f"All Gemini models exhausted. The free-tier daily quota "
        f"(20 req/day per model) resets at midnight UTC. "
        f"Last error: {last_exc}"
    )
