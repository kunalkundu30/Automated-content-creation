# ig-ai-pipeline

A blueprint for the automated AI-Instagram content pipeline. Google Sheets is the
queue every stage reads from and writes to; each script does one job and can be
run on its own or chained together by `main.py`.

## Folder structure

```
ig-ai-pipeline/
├── main.py                        # orchestrator — tops up ideas, routes each row
├── config.py                      # loads secrets/config from environment
├── sheets_client.py               # thin wrapper around the Google Sheets API
├── drive_client.py                # shared Drive upload/download helper
├── gemini_helper.py               # shared Gemini API caller: rate-limit retry + model fallback
├── dashboard.py                   # standalone engagement + cost dashboard (read-only)
├── claude_retry.sh                # auto-retries claude CLI after usage-limit cooldown
├── topic_bank.json                # pillars + seasonal calendar for idea generation
├── avatar_bank.json               # per-pillar avatar definitions (auto-created, committed)
├── pipeline/
│   ├── generate_ideas.py          # engagement + trend signals + topic bank → Gemini ideas
│   ├── trend_research.py          # Gemini web grounding + YouTube trending signal
│   ├── avatar_manager.py          # per-pillar avatar: create, embed in prompts, evolve
│   ├── generate_content.py        # Gemini API: caption + script + avatar-featured asset prompt
│   ├── generate_images.py         # Pollinations.AI: avatar-consistent wide/medium-shot images
│   ├── generate_voice.py          # Google Cloud TTS: script → voiceover + caption timing
│   ├── assemble_video.py          # FFmpeg: images + voice + captions + music → finished reel
│   ├── qa_checks.py               # validates disclosure text, links, char limits
│   ├── publish_instagram.py       # Instagram Graph API: container + publish
│   └── analytics.py               # pulls Insights, logs performance back to sheet
├── assets/music/                  # drop a handful of royalty-free tracks here (see its README)
├── .github/workflows/pipeline.yml # scheduled run (free on a public repo)
├── requirements.txt
├── .env.example                   # copy to .env and fill in for local runs
└── .gitignore
```

## Row lifecycle (the `status` column drives everything)

```
idea -> drafted -> ready -> qa_passed -> published
```

- **idea**: `generate_ideas.py` fills these in automatically whenever the queue runs low.
  It blends three signals and feeds the synthesis to Gemini:
  1. **Engagement analysis** — reads `reach` and `saves` from published rows in the sheet,
     computes per-pillar save-rate (saves ÷ reach), and surfaces the highest-performing
     topics so Gemini can lean into proven directions.
  2. **Live trend research** (`trend_research.py`) — uses Gemini with Google Search
     grounding (free, no extra key) to fetch what is currently performing on Instagram,
     TikTok, Pinterest, and YouTube in the account's content categories. Optionally
     enriched with YouTube Data API v3 trending video titles if `YOUTUBE_API_KEY` is set.
  3. **Topic bank** — `topic_bank.json` provides content pillars and a seasonal calendar
     as the stability layer. If the combined signal suggests an underserved niche, Gemini
     can propose new pillars and the module auto-adds them to `topic_bank.json`.
- **drafted**: `generate_content.py` has filled in `caption` and `script`. One step
  remains before the row moves forward: fill in `asset_type` with `image` or `video`.
  - **image**: the next run auto-fills `asset_prompt` (featuring the pillar's avatar —
    see below) and then generates images via **Pollinations.AI** (free, no API key).
    Images are uploaded to Drive automatically; `images_folder_id` is filled in for you.
  - **video**: the next run auto-fills `asset_prompt` with a ready-to-paste prompt for
    Kling/Runway/Pika that describes the avatar so clips are visually consistent.
    Generate the clip manually, upload to a Drive folder, and paste the folder ID into
    `images_folder_id`. The next run finishes assembly automatically.
- **(still drafted, images_folder_id present)**: voiceover (with synced caption timing)
  and the finished video are assembled automatically with FFmpeg — pan/zoom on each asset,
  word-wrapped burned-in captions, and optional background music — then the row moves to
  `ready`. No CapCut, no manual editing.
- **ready**: `qa_checks.py` runs automatically; passes go to `qa_passed`, failures go
  to `qa_failed` with a reason written to the `qa_notes` column so you can fix and retry.
- **qa_passed**: `publish_instagram.py` publishes it on (or after) `scheduled_date`.
- **published**: `analytics.py` picks it up on its next run and logs performance.

## Per-pillar avatars

Each content pillar has one avatar — a realistic human character who appears
as the consistent subject in every image and video generated for that pillar.
Avatars are defined in `avatar_bank.json` and managed by `pipeline/avatar_manager.py`.

**What's locked:** name, age, skin tone, build, hair colour, distinguishing features,
and voice configuration (TTS language code, voice name, speaking rate). These never
change, even during evolution cycles.

**What adapts:** clothing and setting match the content scene (cosy knitwear for
winter organisation content; gym-adjacent for productivity posts; neutral basics
for everyday desk content). The face and identifying traits stay identical.

**Creation:** Lazy — the first time a pillar reaches the drafted stage, Gemini
generates a character definition tailored to that niche's target demographic and
saves it to `avatar_bank.json`. All subsequent content for that pillar reuses it.
The generated avatar includes a `voice` block that selects a TTS voice matching the
character's nationality and personality (e.g. a French avatar gets a French-accent
Journey or Neural2 voice).

**Embedding:** The visual anchor (a compact description of the character) is
injected into both the Gemini asset-prompt call (so the LLM writes a scene
around the character) and the Pollinations.AI image URL (as a hard constraint
on the subject). All image prompts also enforce a medium/3-quarter/wide shot
framing so the avatar always appears in context — never a headshot or close-up.
For video rows, the generated prompt can be pasted directly into Kling/Runway
so manually-created clips match the same character.

**Voice:** `generate_voice.py` reads the avatar's `voice` block to pick the correct
Google Cloud TTS voice — language code, voice name, and speaking rate — so the
voiceover accent matches the character's background. Falls back to `en-US-Neural2-F`
at 1.05× if the field is absent. If a Journey voice is requested but is not enabled
on the GCP project, the code automatically retries with the Neural2 equivalent.

**Evolution:** After each idea-generation cycle, `avatar_manager.maybe_evolve_avatars()`
checks whether any pillar has ≥5 published posts and ≥30 days since the last
change. If so, Gemini proposes a conservative style update informed by the
pillar's save-rate data and live trend signal. Immutable traits (name, age, skin
tone, build, voice) are enforced in code regardless of the model response. At most 2
avatars evolve per run.

## Gemini API reliability

All Gemini calls go through `gemini_helper.py` instead of calling `google.genai`
directly. The helper handles three error classes transparently:

- **Per-minute rate limit (429):** waits the `retryDelay` from the response body
  and retries the same model, up to 5 times.
- **Daily free-tier quota exhausted:** skips to the next model in the fallback list
  (`gemini-2.5-pro`, `gemini-2.0-flash`).
- **Model deprecated / not found (404):** skips to the next model.

If all models are exhausted the helper raises, and the orchestrator logs the error
and moves on to the next row rather than crashing the whole run.

> **Free-tier note:** The Gemini free tier currently allows ~20 requests/day on
> `gemini-3.6-flash`. If that limit is hit mid-run, upgrade to Gemini
> pay-as-you-go billing in Google AI Studio to remove it.

## Dashboard

`dashboard.py` is a standalone read-only script (imports only `config` and
`sheets_client`, never any pipeline stage modules) that prints a rich terminal
summary:

| Section | Contents |
|---|---|
| Pipeline Overview | Row counts per stage with a fill-bar |
| Published Posts | Per-post: pillar, topic, date, asset type, reach, saves, save % |
| Engagement by Pillar | Aggregated totals + avg reach + save rate |
| Engagement Summary | Overall totals, avg reach/post, top posts by reach and saves |
| Estimated Cost (USD) | TTS chars synthesised (with SSML overhead), Gemini call estimate, all other services at $0 |

```bash
python dashboard.py              # uses sheet-cached metrics (no API calls, instant)
python dashboard.py --refresh    # fetches live reach/saves from Instagram Insights API
```

Safe to run at any time — has zero side-effects on the pipeline.

## What's genuinely automated now vs. what still needs you

Idea generation, avatar management, image generation, voiceover, video assembly,
QA, publishing, and analytics are all hands-off.

Idea generation runs three inputs through Gemini every cycle: engagement data
from the account's own published posts, live trend research via Gemini web
grounding (no extra key) and optionally the YouTube Data API, and the
maintained topic bank. Gemini synthesises these into specific, non-repeating
topics and can auto-extend the topic bank with new pillars when the signals
support it.

For `asset_type=image` rows, images featuring the pillar's avatar are generated
automatically via **Pollinations.AI** (free REST API, no key, no signup) and
uploaded to Drive — zero manual steps. Every image is framed as a medium, 3-quarter,
or wide shot showing the avatar in their environment. Video assembly turns those
assets into a finished, captioned, scored reel. Voiceover uses a TTS voice matched
to the avatar's nationality and personality.

## What stays manual (by design)

**Video clips** (`asset_type=video`). Short AI video clips still require a
free web tool like Kling's daily credits — there is no free, reliable video
generation API to wire in (see the section below). Once you drop the mp4 files
into a Drive folder and paste the folder ID into `images_folder_id`, everything
downstream is automatic. When a paid video API becomes worth the cost, it's a
single-file drop-in replacement for that step.

## One-time setup

1. Create a Google Cloud project. Enable the Sheets API, Drive API, and
   Text-to-Speech API. Optionally also enable the YouTube Data API v3 (same
   project) for YouTube trending signal — see step 5b.
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
5b. (Optional) Enable the YouTube Data API v3 in your Google Cloud project.
   Create an API key restricted to that API and set `YOUTUBE_API_KEY` in your
   `.env` / Actions secrets. This adds per-pillar trending video titles to the
   idea generation signal. 10 000 quota units/day free; this pipeline uses
   ~100 units per pillar per run. Leave `YOUTUBE_API_KEY` blank and the
   YouTube signal is silently skipped — Gemini web grounding still runs.
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

## Claude Code usage limit

If the Claude Code usage limit is reached mid-session, `claude_retry.sh` handles
the wait automatically:

```bash
bash claude_retry.sh
```

It parses the "retry in N minutes" message from Claude's output for an exact wait
time, falling back to `CLAUDE_RETRY_INTERVAL` (default 300 s). It respects
`CLAUDE_MAX_WAIT` (default 86 400 s = 24 h) as a total ceiling before giving up.
