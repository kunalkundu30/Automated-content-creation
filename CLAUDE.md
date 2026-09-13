# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An automated Instagram content pipeline that uses Google Sheets as the orchestration queue. The pipeline runs on GitHub Actions (every 6 hours) or locally and processes content through a staged lifecycle: `idea` → `drafted` → `ready` → `qa_passed` → `published`.

For `asset_type=image` rows the pipeline is fully automated end-to-end. For `asset_type=video` rows, generating the video clip is still manual (no free video generation API exists); everything else is automatic.

## Development Commands

All commands run from `ig-ai-pipeline/`:

```bash
# Install dependencies
pip install -r requirements.txt

# Run the pipeline locally
python main.py

# View engagement + cost dashboard (can be run from any directory)
python dashboard.py
python dashboard.py --refresh   # fetch live Instagram Insights via API

# Copy and fill in environment variables
cp .env.example .env

# Auto-retry claude when usage limit is hit (set interval in seconds)
bash claude_retry.sh
```

FFmpeg must be on PATH (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux).

## Architecture

### Orchestration Flow (`main.py`)

The orchestrator loads all Google Sheets rows, tops up the idea queue if below `IDEA_BUFFER_SIZE`, then dispatches each row to a handler based on its `Status` column. Each handler only processes rows in the expected state, making runs idempotent.

```
main.py
  ├── sheets_client.py    — Google Sheets read/write
  ├── drive_client.py     — Google Drive upload/download/folder-create
  ├── gemini_helper.py    — shared Gemini API caller with model fallback + rate-limit retry
  └── pipeline/
      ├── generate_ideas.py      — engagement + trends + topic bank → Gemini → ideas
      ├── trend_research.py      — Gemini web grounding + YouTube API → live trend signal
      ├── avatar_manager.py      — per-pillar avatar: create / embed / evolve (avatar_bank.json)
      ├── generate_content.py    — Gemini → caption + voiceover script + avatar asset prompt
      ├── generate_images.py     — Pollinations.AI (free) → avatar-consistent images → Drive
      ├── generate_voice.py      — Google Cloud TTS (SSML) → audio file
      ├── assemble_video.py      — FFmpeg → slideshow + word-wrapped captions + audio → video
      ├── qa_checks.py           — validates disclosure, video, link, caption length, date
      ├── publish_instagram.py   — Instagram Graph API two-step publish
      ├── analytics.py           — Instagram Insights → metrics back to sheet
      └── reddit_research.py     — legacy optional signal, superseded by trend_research.py

dashboard.py              — standalone engagement + cost dashboard (no pipeline side-effects)
claude_retry.sh           — shell wrapper: auto-retries claude CLI after usage-limit cooldown
```

### Configuration (`config.py`)

All environment variables are centralized here with defaults. The 16-column sheet layout is defined as constants in this file. Edit `topic_bank.json` to change content pillars or the seasonal calendar — changes take effect on the next run with no code changes required.

### Gemini API Helper (`gemini_helper.py`)

All Gemini calls in `generate_ideas.py`, `generate_content.py`, and `avatar_manager.py` go through this shared helper instead of calling `google.genai` directly. It handles three error classes automatically:

- **Per-minute rate limit (429)**: reads the `retryDelay` from the response and waits, retrying the same model up to 5 times.
- **Daily free-tier quota exhausted (429 + free_tier/PerDay)**: skips to the next model in `_FALLBACK_MODELS`.
- **Model not found / deprecated (404)**: skips to the next model in `_FALLBACK_MODELS`.

Primary model is `config.GEMINI_MODEL` (default `gemini-3.6-flash`). Fallbacks: `gemini-2.5-pro`, `gemini-2.0-flash`.

### Key Runtime Constraints

- Voiceover scripts are capped at 1,200 characters (enforced in `generate_content.py`)
- Video assembly uses temporary directories under `/tmp/video_*`; image generation uses `/tmp/images_*`
- Background music (`assets/music/*.mp3`) is optional — if the folder is empty, videos are assembled with voiceover only. The Instagram Graph API does not grant access to Instagram's native music library (Creator or Business account makes no difference).
- Google Drive auth requires the broad `drive` scope (not `drive.file`) to access shared folders
- Instagram publishing is a two-step API call: create container → poll for processing → publish
- Reddit integration is optional and intended for pre-revenue use only (free PRAW tier); superseded by Gemini web grounding in `trend_research.py`
- Pollinations.AI image generation requires no API key; `DRIVE_FOLDER_ID` must be set so generated images can be uploaded. `IMAGE_COUNT` (default 4) controls images per row.
- Caption/subtitle text is word-wrapped before rendering so it never overflows the 1080px frame width
- Idea generation uses three signals: (1) per-pillar `reach`/`saves` engagement analysis from published sheet rows, (2) Gemini with Google Search grounding for live trend data, (3) optional YouTube Data API v3 trending videos (`YOUTUBE_API_KEY`). Gemini auto-proposes new pillars and `generate_ideas.py` writes them to `topic_bank.json` when signal supports it.
- **Avatar system** (`avatar_bank.json` + `pipeline/avatar_manager.py`): each pillar has one consistent human character. Created lazily by Gemini on first use; visual anchor injected into both Gemini asset prompts and Pollinations URLs. Immutable traits: name, age, skin tone, build. Clothing adapts per scene. Evolved conservatively by `maybe_evolve_avatars()` (≥5 posts, ≥30-day cooldown, max 2 per run) using the same engagement + trend data already in scope during idea generation.
- **Avatar voice block**: each avatar in `avatar_bank.json` carries a `voice` object (`language_code`, `voice_name`, `speaking_rate`) that matches the character's nationality and personality. `generate_voice.py` reads this to select the correct TTS voice per pillar — e.g. a French avatar uses a French-accent voice. Falls back to `en-US-Neural2-F` at 1.05× if the field is absent or null. If a Journey voice is requested but not enabled on the GCP project, the code auto-falls back to the Neural2 equivalent.
- **Image shot types**: `generate_images.py` and `generate_content.py` both enforce medium/3-quarter/wide framing in every Pollinations prompt so images show the avatar in their environment — headshots and close-ups are explicitly excluded.

### Secrets Required

See `.env.example` for the full list. Required groups:
- Google service account JSON (`GOOGLE_SERVICE_ACCOUNT_FILE` path)
- Google Sheets: `SHEET_ID`, `SHEET_TAB_NAME`
- Gemini: `GEMINI_API_KEY`
- Instagram Graph API: `IG_BUSINESS_ACCOUNT_ID`, `IG_ACCESS_TOKEN`
- Google Drive: `DRIVE_FOLDER_ID`
- GitHub: `GH_REPO`, `GH_TOKEN` — used to upload assembled videos as Release assets for a stable public URL
- Google Cloud TTS: included via service account
- Optional: `YOUTUBE_API_KEY` — YouTube Data API v3 key for trending-video signal in idea generation

For GitHub Actions, all secrets are stored in the repository's Actions secrets and passed as environment variables in `.github/workflows/pipeline.yml`.
