"""Stage: published -> (ongoing)

Pulls performance numbers for already-published posts using the same Graph
API access already set up for publishing, so this stays free. Meant to be
run on a slower cadence than the rest of the pipeline (e.g. weekly) since
these numbers take a few days to stabilize.
"""
import requests

import config

_BASE_URL = f"{config.IG_GRAPH_API_BASE}/{config.IG_GRAPH_API_VERSION}"


def fetch_insights(media_id: str) -> dict:
    """Returns engagement metrics for one published post: reach, saves, shares,
    likes, and comments.

    reach + saves + shares come from the /insights endpoint.
    likes + comments come from media fields (like_count, comments_count).
    """
    # ── Insights: reach, saves, shares ──────────────────────────────────────
    url = f"{_BASE_URL}/{media_id}/insights"
    params = {
        "metric": "reach,saved,shares",
        "access_token": config.IG_ACCESS_TOKEN,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    insight_values: dict = {}
    for entry in response.json().get("data", []):
        insight_values[entry["name"]] = entry["values"][0]["value"]

    # ── Media fields: like_count, comments_count ─────────────────────────────
    fields_resp = requests.get(
        f"{_BASE_URL}/{media_id}",
        params={
            "fields": "like_count,comments_count",
            "access_token": config.IG_ACCESS_TOKEN,
        },
        timeout=30,
    )
    fields_resp.raise_for_status()
    fields = fields_resp.json()

    return {
        "reach": insight_values.get("reach", 0),
        "saves": insight_values.get("saved", 0),
        "shares": insight_values.get("shares", 0),
        "likes": fields.get("like_count", 0),
        "comments": fields.get("comments_count", 0),
    }
