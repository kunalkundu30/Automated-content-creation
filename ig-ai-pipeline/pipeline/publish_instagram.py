"""Stage: qa_passed -> published

Implements Instagram's two-step publish flow: create a media container from
a public video URL, then publish it. The video URL comes from a GitHub
Release asset uploaded by assemble_video. Since you're only ever publishing
to your own account, this works with a self-issued long-lived token and no
Meta app review.
"""
import time

import requests

import config

_BASE_URL = f"{config.IG_GRAPH_API_BASE}/{config.IG_GRAPH_API_VERSION}"


def publish_reel(video_url: str, caption: str) -> str:
    """video_url must be a publicly accessible link (GitHub Release asset).
    Returns the published media's ID.
    """
    container_id = _create_container(
        {"media_type": "REELS", "video_url": video_url, "caption": caption}
    )
    _wait_until_container_ready(container_id)
    return _publish_container(container_id)


def _create_container(fields: dict) -> str:
    url = f"{_BASE_URL}/{config.IG_BUSINESS_ACCOUNT_ID}/media"
    params = {**fields, "access_token": config.IG_ACCESS_TOKEN}
    response = requests.post(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()["id"]


def _wait_until_container_ready(
    container_id: str, timeout_seconds: int = 300
) -> None:
    """Video containers process asynchronously — poll status_code until it
    reports FINISHED, or raise if it errors out or takes too long.
    """
    url = f"{_BASE_URL}/{container_id}"
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = requests.get(
            url,
            params={
                "fields": "status_code",
                "access_token": config.IG_ACCESS_TOKEN,
            },
        )
        response.raise_for_status()
        status = response.json().get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise RuntimeError(
                f"Instagram failed to process container {container_id}"
            )
        time.sleep(5)
    raise TimeoutError(
        f"Container {container_id} did not finish processing in time"
    )


def _publish_container(container_id: str) -> str:
    url = f"{_BASE_URL}/{config.IG_BUSINESS_ACCOUNT_ID}/media_publish"
    params = {
        "creation_id": container_id,
        "access_token": config.IG_ACCESS_TOKEN,
    }
    response = requests.post(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()["id"]
