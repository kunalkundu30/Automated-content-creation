# ig-ai-pipeline

A blueprint for the automated AI-Instagram content pipeline. Google Sheets is the
queue every stage reads from and writes to; each script does one job and can be
run on its own or chained together by `main.py`.

## Folder structure

```
ig-ai-pipeline/
├── main.py                        # orchestrator — tops up ideas, routes each row
├── config.py                      # loads secrets/config from environment
├── sheets_client.py                # thin wrapper around the Google Sheets API
├── drive_client.py                 # shared Drive upload/download helper
├── topic_bank.json                 # pillars + seasonal calendar for idea generation
├── pipeline/
│   ├── generate_ideas.py           # Gemini + topic bank: fills the queue with new topics
│   ├── generate_content.py         # Gemini API: caption + script from a topic
│   ├── generate_voice.py           # Google Cloud TTS: script -> voiceover + caption timing
│   ├── assemble_video.py           # FFmpeg: images + voice + captions + music -> finished reel
│   ├── qa_checks.py                # validates disclosure text, links, char limits
│   ├── publish_instagram.py        # Instagram Graph API: container + publish
│   └── analytics.py                # pulls Insights, logs performance back to sheet
├── assets/music/                   # drop a handful of royalty-free tracks here (see its README)
├── .github/workflows/pipeline.yml  # scheduled run (free on a public repo)
├── requirements.txt
├── .env.example                    # copy to .env and fill in for local runs
└── .gitignore
```

## Row lifecycle (the `status` column drives everything)

```
idea -> drafted -> [drafted, waiting on assets] -> ready -> qa_passed -> published
```

- **idea**: `generate_ideas.py` fills these in automatically whenever the queue runs low —
  no manual research step. It blends `topic_bank.json` (pillars + a seasonal calendar) with
  Gemini, and explicitly avoids repeating recent topics pulled from the sheet.
- **drafted**: `generate_content.py` has filled in `caption` and `script`. Two manual
  sub-steps remain before the row can move forward:
  1. Fill in `asset_type` with `image` or `video`. The next pipeline run auto-fills
     `asset_prompt` with a ready-to-paste prompt for the AI tool of your choice
     (Leonardo/Canva for images, Kling/Runway for video).
  2. Create the assets using that prompt, upload them to a Google Drive folder shared
     with the service account, and paste the folder ID into `images_folder_id`.
- **(still drafted, images_folder_id present)**: as soon as `images_folder_id` is filled
  in, the next run automatically generates the voiceover (with synced caption timing) and
  assembles the finished video with FFmpeg — pan/zoom on each asset, burned-in captions,
  and background music if any tracks are present in `assets/music/` — then moves the row
  to `ready`. No CapCut, no manual editing.
- **ready**: `qa_checks.py` runs automatically; passes go to `qa_passed`, failures go
  to `qa_failed` with a reason written to the `qa_notes` column so you can fix and retry.
- **qa_passed**: `publish_instagram.py` publishes it on (or after) `scheduled_date`.
- **published**: `analytics.py` picks it up on its next run and logs performance.

## What's genuinely automated now vs. what still needs you

Idea generation and video assembly are both hands-off. Idea generation blends
a maintained topic bank with Gemini, optionally enriched with real discussion
signal from Reddit's free tier (see the caveat above and in
`pipeline/reddit_research.py`). Video assembly turns whatever visual assets
are sitting in a row's Drive folder — AI images, AI-generated video clips, or
a mix — into a finished, captioned, scored reel.

## What stays manual (by design)

Generating the visual assets themselves. Whether that's AI images
(Canva/Leonardo) or short AI video clips (Kling's free daily web credits,
per the section above), a human is still the one producing the raw visuals
and dropping them into the row's folder — because no free, automatable API
does that reliably yet. Everything downstream of "visual assets exist in a
folder" runs on its own.

## One-time setup

1. Create a Google Cloud project. Enable the Sheets API, Drive API, and
   Text-to-Speech API.
2. Create a service account, download its JSON key as `service-account.json`
   (keep this out of git — it's already in `.gitignore`), and share your
   Google Sheet **and** any Drive folder this pipeline needs to read or write
   (the images folders, the general output folder) with the service
   account's email address, Editor access. The service account needs the
   broader `drive` scope rather than `drive.file` specifically so it can read
   folders you create yourself, not just files it created.
3. Get a Gemini API key from Google AI Studio (free tier, no card needed).
4. Get a long-lived Instagram access token and your Instagram Business
   Account ID by adding your own account as a tester on a Meta developer app
   in Development mode — no app review needed since you're only publishing
   to your own account.
5. (Optional) Add royalty-free, commercially-licensed `.mp3` tracks to
   `assets/music/` for background music. If the folder is empty the pipeline
   still works — videos are assembled with voiceover only. The Instagram Graph
   API does not grant access to Instagram's native music library regardless of
   account type (Creator or Business), so any background music must be tracks
   you supply yourself.
6. (Optional) Register a Reddit app at reddit.com/prefs/apps for the
   research signal in `pipeline/reddit_research.py` — **read that file's
   docstring before enabling it**, it's scoped to Reddit's free tier on
   purpose and needs to be reconsidered before this account is monetized.
   Approval currently runs 2-4 weeks under Reddit's Responsible Builder
   Policy, so this is the slowest piece to bring online, not the fastest.
7. Copy `.env.example` to `.env` and fill in all the values.
8. `pip install -r requirements.txt` and make sure `ffmpeg` is on your PATH
   locally (`brew install ffmpeg` / `apt install ffmpeg` / etc — already
   present on GitHub Actions' runners, so this only matters for local testing).
9. Run `python main.py` locally once to confirm each stage works before
   handing it to the scheduled workflow.

## AI video generation: what's actually free right now

Worth being direct about this rather than wiring in something unreliable.
As of September 2026: Google's Veo has no free tier at all on its developer
API — every call is billed from the first second, the only free access is
small rationed amounts inside Google's own consumer apps. OpenAI's Sora was
never free either, and is being shut down outright — the app closed in
April 2026 and the API sunsets September 24, 2026. Runway, Pika, and Luma
give new accounts a one-time batch of trial credits, not an ongoing free
tier. Kling is the exception with a real recurring free daily allowance,
but it's on their consumer web app, not exposed as a free API — there's
nothing to wire into this codebase.

Given that, and given there's no scale yet to justify a paid video API, the
practical free approach is: generate short clips manually using Kling's
(or a similar tool's) daily free web credits, and drop the resulting mp4
files into the same per-row Drive folder as the images, alongside or
instead of them. `assemble_video.py` already handles a mix of images and
video clips in one folder — it normalizes resolution/fps, lets clips play
at their native length, and fills any remaining time with the still images.
Nothing else in the pipeline needs to change. When there's real budget, a
paid video-gen API becomes a drop-in replacement for that one manual step,
the same upgrade pattern as everything else in this stack.

## Running it on a schedule for free

`.github/workflows/pipeline.yml` runs `main.py` on a cron schedule using
GitHub Actions, which is free and has no minute cap on **public** repositories.
Put all the `.env` values into the repo's Actions secrets instead — never commit
`.env` or `service-account.json`. There's nothing sensitive in the code itself,
only in the secrets, so a public repo is fine here.

Two things to build around:
- Scheduled workflows are auto-disabled after 60 days with no commits to the
  repo — the workflow includes a small step that commits a timestamp file so
  this never happens on its own.
- Keep `main.py` idempotent (safe to run twice) since a scheduled run and a
  manual run could occasionally overlap — every stage below only acts on rows
  in the specific status it expects.
