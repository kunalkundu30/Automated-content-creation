"""Stage: idea -> drafted

Takes a topic + content pillar and produces a caption and a short video
script using the Gemini API free tier.
"""
import re
import time

from google import genai

import config

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
                print(f"Rate limited — waiting {wait:.0f}s "
                      f"(attempt {attempt + 1}/5)...")
                time.sleep(wait)
            else:
                raise


_PROMPT_TEMPLATE = """\
You write short-form Instagram content for an AI-curated account
about {pillar}.
Topic: {topic}

Return exactly two sections, plainly labeled, nothing else:

CAPTION:
<a scroll-stopping 2-3 sentence caption, conversational tone, ending with a
soft call to action to save or share. Do not use hashtags here.>

SCRIPT:
<a 30-45 second voiceover script for a reel about this topic, written to be
read aloud, roughly 90-120 words>
"""


def generate_caption_and_script(topic: str, pillar: str) -> dict:
    """Returns {"caption": str, "script": str}. Raises ValueError if the
    model's response doesn't contain both expected sections, so a malformed
    generation fails loudly instead of silently writing garbage to the sheet.
    """
    prompt = _PROMPT_TEMPLATE.format(pillar=pillar, topic=topic)
    text = _gemini_generate(prompt)

    if "CAPTION:" not in text or "SCRIPT:" not in text:
        raise ValueError(f"Unexpected Gemini response format:\n{text}")

    caption_part = text.split("CAPTION:")[1].split("SCRIPT:")[0].strip()
    script_part = text.split("SCRIPT:")[1].strip()

    # Bake the disclosure line in here rather than trusting every future
    # prompt edit to remember it — qa_checks.py verifies it made it through.
    caption_with_disclosure = f"{caption_part}\n\n{config.AI_DISCLOSURE_TEXT}"

    return {"caption": caption_with_disclosure, "script": script_part}


_ASSET_PROMPT_TEMPLATES = {
    "image": """\
You write image generation prompts for AI tools (Leonardo, Midjourney, Canva AI).

Pillar: {pillar}
Topic: {topic}
Caption: {caption}
Script: {script}

Write one image generation prompt for a 9:16 portrait still that works as a
background visual for this Instagram Reel. Requirements:
- Aspirational and aesthetically pleasing
- Matches the topic mood and pillar style
- No text, logos, or people reading (captions are burned in later)
- Enough visual detail to be one of 4-6 consistent images in a slideshow

Return only the prompt text, no label or explanation.
""",
    "video": """\
You write short video generation prompts for AI tools (Kling, Runway, Pika).

Pillar: {pillar}
Topic: {topic}
Caption: {caption}
Script: {script}

Write one video clip prompt for a 5-15 second, 9:16 portrait clip that works
as a visual for this Instagram Reel. Requirements:
- Shows relevant slow motion (pan, zoom, reveal, transition)
- Aesthetically pleasing and on-brand for the pillar
- No on-screen text or speaking (voiceover and captions added separately)
- Suitable as one of 2-4 clips cut together in a reel

Return only the prompt text, no label or explanation.
""",
}


def generate_asset_prompt(
    topic: str, pillar: str, caption: str, script: str, asset_type: str
) -> str:
    """Returns a ready-to-paste prompt for an image or video AI generation tool.
    asset_type must be 'image' or 'video'.
    """
    key = asset_type.lower().strip()
    template = _ASSET_PROMPT_TEMPLATES.get(key)
    if not template:
        raise ValueError(
            f"asset_type must be 'image' or 'video', got: {asset_type!r}"
        )
    prompt = template.format(
        pillar=pillar, topic=topic, caption=caption, script=script
    )
    return _gemini_generate(prompt).strip()
