"""Stage: ready -> qa_passed (or qa_failed)

Everything here is deliberately simple and literal — the point isn't clever
validation, it's catching the specific mistakes that are expensive later:
a missing disclosure line, a broken affiliate link, a missing video.
"""
import re

import requests

import config

_URL_PATTERN = re.compile(r"^https?://\S+$")


def run_qa(row: dict) -> tuple[bool, str]:
    """Returns (passed, reason). `reason` is empty on success and a
    human-readable explanation on failure, meant to be written straight into
    the sheet's qa_notes column.
    """
    failures = []

    if config.AI_DISCLOSURE_TEXT not in row.get("caption", ""):
        failures.append("Missing AI disclosure line in caption")

    if not row.get("video_path"):
        failures.append("No video_path set — video hasn't been assembled yet")

    affiliate_link = row.get("affiliate_link", "")
    if affiliate_link:
        if not _URL_PATTERN.match(affiliate_link):
            failures.append(f"Affiliate link is not a valid URL: {affiliate_link}")
        elif not _link_is_reachable(affiliate_link):
            failures.append(f"Affiliate link did not return a working response: {affiliate_link}")

    caption = row.get("caption", "")
    if len(caption) > 2200:
        failures.append(f"Caption is {len(caption)} chars — over Instagram's 2,200 limit")

    if not row.get("scheduled_date"):
        failures.append("No scheduled_date set")

    if failures:
        return False, "; ".join(failures)
    return True, ""


def _link_is_reachable(url: str) -> bool:
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        return response.status_code < 400
    except requests.RequestException:
        return False
