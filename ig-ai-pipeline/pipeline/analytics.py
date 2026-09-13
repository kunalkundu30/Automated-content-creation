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
    """Returns the subset of metrics this pipeline tracks: reach and saves.
    Extend the `metric` param below if you want more (likes, comments, shares).
    """
    url = f"{_BASE_URL}/{media_id}/insights"
    params = {"metric": "reach,saved", "access_token": config.IG_ACCESS_TOKEN}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    values = {}
    for entry in response.json().get("data", []):
        values[entry["name"]] = entry["values"][0]["value"]
    return {"reach": values.get("reach", 0), "saves": values.get("saved", 0)}
