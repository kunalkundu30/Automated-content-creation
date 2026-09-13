"""Uploads assembled video files to GitHub Releases as a free, public host.

Uses the same repo that runs the pipeline. A release with tag
"pipeline-media" accumulates all video assets — it's created automatically
on the first run if it doesn't exist yet.

In GitHub Actions, set GH_TOKEN=${{ secrets.GITHUB_TOKEN }} (write access
is granted via the workflow's `permissions: contents: write` setting).
Locally, set GH_TOKEN to a Personal Access Token with `repo` scope.
"""
import requests

import config

_API_BASE = "https://api.github.com"
_UPLOAD_BASE = "https://uploads.github.com"
_RELEASE_TAG = "pipeline-media"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_or_create_release() -> dict:
    """Returns the pipeline-media release object, creating it if needed."""
    repo = config.GH_REPO
    r = requests.get(
        f"{_API_BASE}/repos/{repo}/releases/tags/{_RELEASE_TAG}",
        headers=_headers(),
        timeout=15,
    )
    if r.status_code == 200:
        return r.json()

    r = requests.post(
        f"{_API_BASE}/repos/{repo}/releases",
        headers=_headers(),
        json={
            "tag_name": _RELEASE_TAG,
            "name": "Pipeline Media Assets",
            "body": "Auto-generated release holding assembled video files.",
            "draft": False,
            "prerelease": True,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def upload_video(local_path: str, filename: str) -> str:
    """Uploads a video to the pipeline-media release and returns its public
    browser_download_url. Idempotent: deletes any existing asset with the
    same name before uploading so re-runs don't stack up duplicate files.
    """
    release = _get_or_create_release()
    release_id = release["id"]

    for asset in release.get("assets", []):
        if asset["name"] == filename:
            requests.delete(
                f"{_API_BASE}/repos/{config.GH_REPO}"
                f"/releases/assets/{asset['id']}",
                headers=_headers(),
                timeout=15,
            )
            break

    with open(local_path, "rb") as f:
        video_bytes = f.read()

    r = requests.post(
        f"{_UPLOAD_BASE}/repos/{config.GH_REPO}"
        f"/releases/{release_id}/assets?name={filename}",
        headers={**_headers(), "Content-Type": "video/mp4"},
        data=video_bytes,
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["browser_download_url"]
