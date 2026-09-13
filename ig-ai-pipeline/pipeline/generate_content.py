"""Stage: idea -> drafted

Takes a topic + content pillar and produces a caption and a short video
script using the Gemini API free tier.
"""
import config
from gemini_helper import generate_content as _gemini_generate


_PROMPT_TEMPLATE = """\
You write short-form Instagram content for an AI-curated account
about {pillar}.
Topic: {topic}
Content language: Write all content in {language}.

Return exactly two sections, plainly labeled, nothing else:

CAPTION:
<a scroll-stopping 2-3 sentence caption in {language}, conversational tone,
ending with a soft call to action to save or share. Do not use hashtags here.>

SCRIPT:
<a 30-45 second voiceover script in {language} for a reel about this topic,
written to be read aloud, roughly 90-120 words>
"""


def generate_caption_and_script(
    topic: str, pillar: str, avatar: dict | None = None
) -> dict:
    """Returns {"caption": str, "script": str}. Raises ValueError if the
    model's response doesn't contain both expected sections, so a malformed
    generation fails loudly instead of silently writing garbage to the sheet.
    Uses the avatar's content_language if provided, otherwise defaults to English.
    """
    language = "English"
    if avatar:
        language = avatar.get("voice", {}).get("content_language", "English")
    prompt = _PROMPT_TEMPLATE.format(pillar=pillar, topic=topic, language=language)
    text = _gemini_generate(prompt)

    if "CAPTION:" not in text or "SCRIPT:" not in text:
        raise ValueError(f"Unexpected Gemini response format:\n{text}")

    caption_part = text.split("CAPTION:")[1].split("SCRIPT:")[0].strip()
    script_part = text.split("SCRIPT:")[1].strip()

    # Bake the disclosure line in here rather than trusting every future
    # prompt edit to remember it — qa_checks.py verifies it made it through.
    caption_with_disclosure = f"{caption_part}\n\n{config.AI_DISCLOSURE_TEXT}"

    return {"caption": caption_with_disclosure, "script": script_part}


_IMAGE_PROMPT_TEMPLATE = """\
You write image generation prompts for AI tools (Flux, Midjourney, Leonardo).

Pillar: {pillar}
Topic: {topic}
Caption: {caption}
Script: {script}

AVATAR — this character must appear as the main subject in every image for
this pillar. Keep the face, hair, and distinguishing features exactly as
described; only clothing and setting change per scene.
{avatar_section}

Write one image generation prompt for a 9:16 portrait still featuring the
avatar in a scene relevant to the topic. Requirements:
- SHOT TYPE: use a medium shot (waist-up), 3/4 shot, or wide/establishing
  shot that shows the avatar in their environment. NEVER a headshot,
  portrait close-up, or face-only frame — the setting and body language
  must be visible and tell the story.
- Show the avatar actively engaging with the topic (demonstrating, using,
  organising, or reacting — not just posing)
- The background and environment must clearly reflect the topic context
- Aspirational lifestyle photography, natural light, Instagram aesthetic
- Clothing fits the content scene, following the avatar's style rules
- No text overlays or UI elements (captions are burned in separately)
- Enough visual detail to be one of 4-6 consistent slideshow images

Return only the prompt text, no label or explanation.
"""

_VIDEO_PROMPT_TEMPLATE = """\
You write short video generation prompts for AI tools (Kling, Runway, Pika).

Pillar: {pillar}
Topic: {topic}
Caption: {caption}
Script: {script}

AVATAR — this character must appear in every video clip for this pillar.
Keep face, hair, and distinguishing features exactly as described.
{avatar_section}

Write one video clip prompt for a 5-15 second, 9:16 portrait clip featuring
the avatar. Requirements:
- Show the avatar interacting with or demonstrating the topic
- Intentional camera movement or action (pan, reveal, hands demonstrating)
- No on-screen text or speaking (voiceover and captions added separately)
- Clothing fits the scene, following the avatar's style rules
- Suitable as one of 2-4 clips cut together in a reel

Return only the prompt text, no label or explanation.
"""

_NO_AVATAR_NOTE = (
    "(no avatar defined yet for this pillar — "
    "use a consistent, aspirational human subject appropriate to the niche)"
)


def generate_asset_prompt(
    topic: str,
    pillar: str,
    caption: str,
    script: str,
    asset_type: str,
    avatar: dict | None = None,
) -> str:
    """Returns a ready-to-paste prompt for an image or video AI generation
    tool. `asset_type` must be 'image' or 'video'. When `avatar` is provided
    the prompt instructs the model to feature that character consistently.
    """
    from pipeline.avatar_manager import build_visual_anchor

    key = asset_type.lower().strip()
    if key == "image":
        template = _IMAGE_PROMPT_TEMPLATE
    elif key == "video":
        template = _VIDEO_PROMPT_TEMPLATE
    else:
        raise ValueError(
            f"asset_type must be 'image' or 'video', got: {asset_type!r}"
        )

    avatar_section = build_visual_anchor(avatar) if avatar else _NO_AVATAR_NOTE
    prompt = template.format(
        pillar=pillar,
        topic=topic,
        caption=caption,
        script=script,
        avatar_section=avatar_section,
    )
    return _gemini_generate(prompt).strip()
