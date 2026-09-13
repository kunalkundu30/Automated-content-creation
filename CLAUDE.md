# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

An automated Instagram content pipeline that uses Google Sheets as the orchestration queue. The pipeline runs on GitHub Actions (every 6 hours) or locally and processes content through a staged lifecycle: `idea` → `drafted` → `ready` → `qa_passed` → `published`.

The only manual step is generating visual assets (images/video clips) and placing them in a Google Drive folder. Everything else is automated.

## Development Commands

All commands run from `ig-ai-pipeline/`:

```bash
# Install dependencies
pip install -r requirements.txt

# Run the pipeline locally
python main.py

# Copy and fill in environment variables
cp .env.example .env
```

FFmpeg must be on PATH (`brew install ffmpeg` on macOS, `apt install ffmpeg` on Linux).

## Architecture

### Orchestration Flow (`main.py`)

The orchestrator loads all Google Sheets rows, tops up the idea queue if below `IDEA_BUFFER_SIZE`, then dispatches each row to a handler based on its `Status` column. Each handler only processes rows in the expected state, making runs idempotent.

```
main.py
  ├── sheets_client.py    — Google Sheets read/write
  ├── drive_client.py     — Google Drive upload/download
  └── pipeline/
      ├── generate_ideas.py      — Gemini + topic_bank.json → new idea rows
      ├── generate_content.py    — Gemini → caption + voiceover script
      ├── generate_voice.py      — Google Cloud TTS (SSML) → audio file
      ├── assemble_video.py      — FFmpeg → slideshow + captions + audio → video
      ├── qa_checks.py           — validates disclosure, video, link, caption length, date
      ├── publish_instagram.py   — Instagram Graph API two-step publish
      ├── analytics.py           — Instagram Insights → metrics back to sheet
      └── reddit_research.py     — optional PRAW signal for idea generation
```

### Configuration (`config.py`)

All environment variables are centralized here with defaults. The 16-column sheet layout is defined as constants in this file. Edit `topic_bank.json` to change content pillars or the seasonal calendar — changes take effect on the next run with no code changes required.

### Key Runtime Constraints

- Voiceover scripts are capped at 1,200 characters (enforced in `generate_content.py`)
- Video assembly uses temporary directories under `/tmp/video_*`
- Background music (`assets/music/*.mp3`) is optional — if the folder is empty, videos are assembled with voiceover only. The Instagram Graph API does not grant access to Instagram's native music library (Creator or Business account makes no difference).
- Google Drive auth requires the broad `drive` scope (not `drive.file`) to access shared folders
- Instagram publishing is a two-step API call: create container → poll for processing → publish
- Reddit integration is optional and intended for pre-revenue use only (free PRAW tier)

### Secrets Required

See `.env.example` for the full list. Required groups:
- Google service account JSON (`GOOGLE_APPLICATION_CREDENTIALS` path)
- Google Sheets: `SHEET_ID`, `SHEET_TAB`
- Gemini: `GEMINI_API_KEY`
- Instagram Graph API: `IG_ACCOUNT_ID`, `IG_ACCESS_TOKEN`
- Google Drive: `DRIVE_FOLDER_ID`
- Google Cloud TTS: included via service account

For GitHub Actions, all secrets are stored in the repository's Actions secrets and passed as environment variables in `.github/workflows/pipeline.yml`.
