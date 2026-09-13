"""Stage: drafted -> (feeds straight into video assembly)

Turns a script into a voiceover audio file using Google Cloud Text-to-Speech
(chosen over ElevenLabs' free tier specifically because it carries no
non-commercial restriction), and also returns word-group-level timing so the
video stage can burn in synced captions — using Cloud TTS's SSML <mark>
timepointing feature rather than a separate transcription pass.
"""
from google.cloud import texttospeech_v1beta1 as texttospeech

import config

_client = texttospeech.TextToSpeechClient.from_service_account_file(
    config.GOOGLE_SERVICE_ACCOUNT_FILE
)

# caption burst length — short bursts read easier on a reel
_WORDS_PER_CHUNK = 6


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


def generate_voiceover_with_timing(script: str, row_id: str) -> dict:
    """Returns {"local_audio_path", "caption_chunks"}, where caption_chunks
    is a list of {"text", "start", "end"} in seconds — ready to hand
    straight to assemble_video.build_video().
    """
    if len(script) > 1200:
        raise RuntimeError(
            f"Script for row {row_id} is {len(script)} chars — "
            "too long for a single reel voiceover, trim it first."
        )

    chunks = _chunk_script(script)
    ssml = _build_ssml(chunks)

    synthesis_input = texttospeech.SynthesisInput(ssml=ssml)
    voice = texttospeech.VoiceSelectionParams(
        language_code="en-US", name="en-US-Neural2-F"
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3
    )

    timepoint_type = (
        texttospeech.SynthesizeSpeechRequest.TimepointType.SSML_MARK
    )
    response = _client.synthesize_speech(
        request={
            "input": synthesis_input,
            "voice": voice,
            "audio_config": audio_config,
            "enable_time_pointing": [timepoint_type],
        }
    )

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
