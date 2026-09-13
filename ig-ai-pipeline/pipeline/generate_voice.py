"""Stage: drafted -> (feeds straight into video assembly)

Turns a script into a voiceover audio file using Google Cloud Text-to-Speech
(chosen over ElevenLabs' free tier specifically because it carries no
non-commercial restriction), and also returns word-group-level timing so the
video stage can burn in synced captions — using Cloud TTS's SSML <mark>
timepointing feature rather than a separate transcription pass.

Voice selection uses the avatar's "voice" field (language_code, voice_name,
speaking_rate) so the TTS accent and language match the character's
personality and nationality.

Journey voices are the most natural-sounding Google TTS voices, but they
require explicit enablement in some GCP projects. If a requested voice is
unavailable (400 INVALID_ARGUMENT), the code automatically falls back to
the Neural2 equivalent for the same language and gender. Neural2 voices
are universally available wherever the TTS API is enabled.
"""
from google.cloud import texttospeech_v1beta1 as texttospeech

import config

_client = texttospeech.TextToSpeechClient.from_service_account_file(
    config.GOOGLE_SERVICE_ACCOUNT_FILE
)

# caption burst length — short bursts read easier on a reel
_WORDS_PER_CHUNK = 6

# Default voice: Neural2-F is confirmed available across all GCP projects
# that have TTS enabled. Neural2 is near-human quality at 1.05× speaking rate.
# Update to "en-US-Journey-F" here if Journey voices are enabled for the
# GCP project (list voices to check: texttospeech_client.list_voices()).
_DEFAULT_LANGUAGE_CODE = "en-US"
_DEFAULT_VOICE_NAME = "en-US-Neural2-F"
_DEFAULT_SPEAKING_RATE = 1.05

# Suffix mapping used to build a Neural2 fallback from a Journey voice name.
# Journey voices use -F (female) and -D (male); Neural2 uses -F and -D too.
_JOURNEY_TO_NEURAL2_SUFFIX = {"F": "F", "D": "D"}


def _neural2_fallback(voice_name: str, language_code: str) -> tuple[str, str]:
    """Given a Journey voice name, returns a (language_code, voice_name) pair
    for the Neural2 equivalent. Falls back to the universal default if the
    language has no direct Neural2 mapping.

    Examples:
      en-US-Journey-F  →  (en-US, en-US-Neural2-F)
      fr-FR-Journey-D  →  (en-US, en-US-Neural2-D)  ← no fr-FR Neural2
    """
    # Extract gender suffix (last character after the final hyphen)
    gender = voice_name.rsplit("-", 1)[-1]  # "F" or "D"
    neural2_suffix = _JOURNEY_TO_NEURAL2_SUFFIX.get(gender, "F")

    # Use the same language code if en-* (Neural2 covers all English variants)
    if language_code.startswith("en-"):
        return language_code, f"{language_code}-Neural2-{neural2_suffix}"
    # For non-English Journey voices with no Neural2 equivalent, fall back to
    # the default English Neural2 voice so TTS always produces something.
    return _DEFAULT_LANGUAGE_CODE, f"{_DEFAULT_LANGUAGE_CODE}-Neural2-{neural2_suffix}"


def _chunk_script(script: str) -> list[str]:
    words = script.split()
    return [
        " ".join(words[i:i + _WORDS_PER_CHUNK])
        for i in range(0, len(words), _WORDS_PER_CHUNK)
    ]


def _build_ssml(chunks: list[str]) -> str:
    marked = "".join(
        f'<mark name="c{i}"/>{chunk} ' for i, chunk in enumerate(chunks)
    )
    return f'<speak>{marked}<mark name="end"/></speak>'


def _synthesize(
    ssml: str,
    language_code: str,
    voice_name: str,
    speaking_rate: float,
) -> "texttospeech.SynthesizeSpeechResponse":
    """One TTS call. Raises the original exception on failure."""
    synthesis_input = texttospeech.SynthesisInput(ssml=ssml)
    voice = texttospeech.VoiceSelectionParams(
        language_code=language_code, name=voice_name
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
        speaking_rate=speaking_rate,
    )
    timepoint_type = (
        texttospeech.SynthesizeSpeechRequest.TimepointType.SSML_MARK
    )
    return _client.synthesize_speech(
        request={
            "input": synthesis_input,
            "voice": voice,
            "audio_config": audio_config,
            "enable_time_pointing": [timepoint_type],
        }
    )


def generate_voiceover_with_timing(
    script: str, row_id: str, avatar: dict | None = None
) -> dict:
    """Returns {"local_audio_path", "caption_chunks"}, where caption_chunks
    is a list of {"text", "start", "end"} in seconds — ready to hand
    straight to assemble_video.build_video().

    When `avatar` is provided its "voice" field drives voice selection:
      - language_code / voice_name → accent and language of the TTS voice
      - speaking_rate → delivery pace (1.0 natural, 1.05–1.1 energetic)
    Falls back to en-US-Neural2-F at 1.05× if the avatar has no voice config.
    If the requested voice is unavailable (Journey not enabled), the code
    automatically retries with the Neural2 equivalent.
    """
    if len(script) > 1200:
        raise RuntimeError(
            f"Script for row {row_id} is {len(script)} chars — "
            "too long for a single reel voiceover, trim it first."
        )

    voice_cfg = (avatar or {}).get("voice") or {}
    language_code = voice_cfg.get("language_code", _DEFAULT_LANGUAGE_CODE)
    voice_name = voice_cfg.get("voice_name", _DEFAULT_VOICE_NAME)
    speaking_rate = float(
        voice_cfg.get("speaking_rate", _DEFAULT_SPEAKING_RATE)
    )

    chunks = _chunk_script(script)
    ssml = _build_ssml(chunks)

    try:
        response = _synthesize(ssml, language_code, voice_name, speaking_rate)
    except Exception as exc:
        # Journey voices are not universally available — retry with Neural2
        if "400" in str(exc) and "Journey" in voice_name:
            fb_lc, fb_vn = _neural2_fallback(voice_name, language_code)
            print(
                f"  TTS: {voice_name} unavailable (Journey not enabled) — "
                f"falling back to {fb_vn}."
            )
            response = _synthesize(ssml, fb_lc, fb_vn, speaking_rate)
        else:
            raise

    local_path = f"/tmp/voice_{row_id}.mp3"
    with open(local_path, "wb") as f:
        f.write(response.audio_content)

    # response.timepoints gives each mark's start time (c0, c1, ..., end)
    times = {tp.mark_name: tp.time_seconds for tp in response.timepoints}
    caption_chunks = []
    for i, text in enumerate(chunks):
        start = times.get(f"c{i}", 0.0)
        end = times.get(f"c{i + 1}", times.get("end", start + 2.0))
        caption_chunks.append({"text": text, "start": start, "end": end})

    return {"local_audio_path": local_path, "caption_chunks": caption_chunks}
