"""Stage: drafted (image type) — auto-generate images featuring the avatar

Uses Pollinations.AI to generate images for free with no API key or signup
required. The API is a simple GET request that returns a JPEG directly.

API: GET https://image.pollinations.ai/prompt/{encoded_prompt}
     Optional query params: width, height, nologo, seed, model

When an `avatar` dict is supplied, the pillar's visual anchor is appended to
the Pollinations prompt as a hard consistency constraint. This ensures the same
character appears in every image regardless of how specific (or vague) the
LLM-generated asset_prompt is about the subject.

Images are generated at 1080x1920 (9:16 portrait, Instagram Reel spec) and
saved to a local temp directory. The caller passes this directory straight to
assemble_video.py, so no Google Drive upload is required for image rows.
Drive is only needed for video rows where the user manually uploads clips.

IMAGE_COUNT (default 4) controls how many images are generated per row.
Different seeds produce scene variation while the avatar anchor keeps the
subject consistent.
"""
import os
import time
import uuid
from urllib.parse import quote

import requests

import config

_POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
_WIDTH, _HEIGHT = 1080, 1920
_TIMEOUT_SECONDS = 90   # Pollinations can be slow on first render
_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 5


def generate_images_locally(
    prompt: str,
    row_id: str,
    avatar: dict | None = None,
) -> str:
    """Generates IMAGE_COUNT images via Pollinations.AI into a local temp
    directory and returns its path. No Drive upload — the caller passes this
    path directly to assemble_video.build_video(), which cleans it up after
    assembly.

    When `avatar` is provided, the visual anchor string is appended to the
    Pollinations prompt so the same character appears in every image.
    Images use different seeds for scene variety while keeping the subject
    anchored. Named 01.jpg, 02.jpg, ... for predictable slideshow order.

    Raises RuntimeError if image generation fails for all retries on any image.
    """
    full_prompt = _build_prompt(prompt, avatar)
    work_dir = f"/tmp/images_{row_id}_{uuid.uuid4().hex[:8]}"
    os.makedirs(work_dir, exist_ok=True)
    image_paths = _download_images(full_prompt, work_dir)
    print(f"  Pollinations: generated {len(image_paths)} images → {work_dir}")
    return work_dir


def _build_prompt(base_prompt: str, avatar: dict | None) -> str:
    """Appends shot-framing guidance and the avatar anchor to the Pollinations
    prompt. The framing clause forces a full-body or environmental composition
    so Flux/Pollinations doesn't default to a face close-up, which is its
    tendency when physical descriptors dominate the prompt.
    """
    # Framing instruction placed before the avatar anchor so it has priority
    framing = (
        "full body or 3/4 body visible, person shown in environment, "
        "environmental lifestyle photography, wide or medium shot"
    )
    if not avatar:
        return f"{base_prompt.rstrip('.')}. {framing}."

    from pipeline.avatar_manager import build_visual_anchor
    anchor = build_visual_anchor(avatar)
    return f"{base_prompt.rstrip('.')}. {framing}. Featuring {anchor}"


def _download_images(prompt: str, work_dir: str) -> list[str]:
    """Downloads IMAGE_COUNT images with different seeds and saves locally.
    Returns local paths in filename-sorted order (01.jpg, 02.jpg, ...).
    """
    encoded = quote(prompt, safe="")
    paths = []

    for i in range(config.IMAGE_COUNT):
        # Spread seeds so images have scene variety but the same character
        seed = (i * 1000) + 42
        url = (
            f"{_POLLINATIONS_BASE}/{encoded}"
            f"?width={_WIDTH}&height={_HEIGHT}&nologo=true&seed={seed}"
        )
        local_path = os.path.join(work_dir, f"{i + 1:02d}.jpg")
        _fetch_with_retry(url, local_path, image_index=i + 1)
        paths.append(local_path)

    return paths


def _fetch_with_retry(url: str, dest_path: str, image_index: int) -> None:
    """GETs the image URL, retrying on transient failures."""
    last_exc: Exception | None = None

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=_TIMEOUT_SECONDS)
            resp.raise_for_status()
            if not resp.content:
                raise ValueError(
                    "Pollinations returned an empty response body"
                )
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                wait = _RETRY_DELAY * (attempt + 1)
                print(
                    f"  Pollinations image {image_index}: "
                    f"attempt {attempt + 1} failed ({exc}), "
                    f"retrying in {wait}s..."
                )
                time.sleep(wait)

    raise RuntimeError(
        f"Failed to generate image {image_index} after "
        f"{_RETRY_ATTEMPTS} attempts: {last_exc}"
    )
