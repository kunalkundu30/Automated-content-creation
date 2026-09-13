#!/usr/bin/env python3
"""
Standalone Instagram pipeline dashboard.

Shows:
  - Pipeline status breakdown (rows per lifecycle stage)
  - Per-post engagement (reach, saves, save rate)
  - Engagement aggregated by content pillar
  - Top performing posts
  - Estimated project costs broken down by service

Usage (from ig-ai-pipeline/):
    python dashboard.py                # read metrics cached in the sheet
    python dashboard.py --refresh      # fetch fresh Instagram Insights via API

Note: this file intentionally imports only infrastructure modules (config,
sheets_client) and not any pipeline stage modules, so it can be run safely
at any time without side-effects.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Always run from the ig-ai-pipeline/ directory so relative paths in
# sheets_client (service-account.json, .env) resolve correctly.
_script_dir = Path(__file__).parent
os.chdir(_script_dir)
sys.path.insert(0, str(_script_dir))

import requests  # noqa: E402
from rich import box  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

import config  # noqa: E402
import sheets_client  # noqa: E402

# Use at least 120 cols so all table columns fit; expand for wider terminals.
_MIN_WIDTH = 120
_term_cols = shutil.get_terminal_size((_MIN_WIDTH, 24)).columns
console = Console(width=max(_MIN_WIDTH, _term_cols))

# ── Google Cloud TTS pricing (Neural2) ────────────────────────────────────
# https://cloud.google.com/text-to-speech/pricing
# Standard: $4 / 1 M chars  (first 4 M free / month)
# WaveNet:  $16 / 1 M chars (first 1 M free / month)
# Neural2:  $16 / 1 M chars (first 100 K free / month)
# Journey:  $30 / 1 M chars (first 100 K free / month)
_TTS_NEURAL2_RATE_PER_CHAR = 16.00 / 1_000_000
_TTS_NEURAL2_FREE_CHARS = 100_000  # per calendar month

# Gemini free tier: 15 req/min, 1 500 req/day for gemini-2.0-flash (2026)
# All other costs are $0 on the free tiers this pipeline uses.

_IMAGE_COUNT = int(os.environ.get("IMAGE_COUNT", "4"))


# ─────────────────────────────────────────────────────────────────────────────
# Instagram Insights helper
# ─────────────────────────────────────────────────────────────────────────────

_IG_BASE = f"{config.IG_GRAPH_API_BASE}/{config.IG_GRAPH_API_VERSION}"


def _fetch_insights(media_id: str) -> dict:
    """Call the Instagram Graph API for reach + saves on one post."""
    url = f"{_IG_BASE}/{media_id}/insights"
    params = {
        "metric": "reach,saved",
        "access_token": config.IG_ACCESS_TOKEN,
    }
    try:
        resp = requests.get(url, params=params, timeout=20)
        resp.raise_for_status()
        vals: dict[str, int] = {}
        for entry in resp.json().get("data", []):
            vals[entry["name"]] = entry["values"][0]["value"]
        return {"reach": vals.get("reach", 0), "saves": vals.get("saved", 0)}
    except Exception as exc:  # noqa: BLE001
        return {"reach": 0, "saves": 0, "error": str(exc)}


def _refresh_insights(published_rows: list[dict]) -> None:
    """Mutate published_rows in-place with fresh API data."""
    total = len(published_rows)
    for i, row in enumerate(published_rows, 1):
        media_id = row.get("media_id", "")
        if not media_id:
            continue
        result = _fetch_insights(media_id)
        if "error" in result:
            console.print(
                f"  [{i}/{total}] {media_id}: [red]{result['error']}[/red]"
            )
        else:
            row["reach"] = result["reach"]
            row["saves"] = result["saves"]
            console.print(
                f"  [{i}/{total}] {media_id}: "
                f"reach=[yellow]{result['reach']:,}[/yellow]  "
                f"saves=[magenta]{result['saves']:,}[/magenta]"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Table builders
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline_status_table(all_rows: list[dict]) -> Table:
    stage_order = [
        "idea", "drafted", "ready", "qa_passed", "published", "qa_failed",
    ]
    stage_style = {
        "idea": "white",
        "drafted": "blue",
        "ready": "yellow",
        "qa_passed": "cyan",
        "published": "green",
        "qa_failed": "red",
    }

    counts: dict[str, int] = {}
    for row in all_rows:
        s = row.get("status") or "unknown"
        counts[s] = counts.get(s, 0) + 1

    total = len(all_rows)

    tbl = Table(
        title="Pipeline Overview",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold",
    )
    tbl.add_column("Stage", min_width=12)
    tbl.add_column("Count", justify="right", width=7)
    tbl.add_column("Share", justify="right", width=7)
    tbl.add_column("", min_width=24)  # progress bar

    for stage in stage_order:
        n = counts.pop(stage, 0)
        pct = n / total if total else 0
        bar_fill = int(pct * 22)
        color = stage_style.get(stage, "white")
        bar = (
            f"[{color}]" + "█" * bar_fill + "[/]"
            + "[dim]░[/dim]" * (22 - bar_fill)
        )
        tbl.add_row(
            f"[{color}]{stage}[/]",
            str(n),
            f"{pct:.0%}",
            bar,
        )

    for stage, n in counts.items():  # unexpected statuses
        tbl.add_row(stage, str(n), f"{n/total:.0%}" if total else "0%", "")

    tbl.add_section()
    tbl.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]", "", "")
    return tbl


def _engagement_table(published_rows: list[dict]) -> Table:
    tbl = Table(
        title="Published Posts — Engagement",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold",
        row_styles=["", "dim"],
    )
    tbl.add_column("#", width=3, justify="right", style="dim")
    tbl.add_column("Pillar", min_width=22, no_wrap=True, style="bold")
    tbl.add_column("Topic", min_width=30)
    tbl.add_column("Published", width=12, style="green")
    tbl.add_column("Asset", width=6, justify="center")
    tbl.add_column("Reach", width=9, justify="right", style="yellow")
    tbl.add_column("Saves", width=7, justify="right", style="magenta")
    tbl.add_column("Save %", width=8, justify="right")

    def _save_pct(reach: int, saves: int) -> str:
        return f"{saves / reach * 100:.1f}%" if reach else "—"

    for i, row in enumerate(published_rows, 1):
        reach = int(row.get("reach") or 0)
        saves = int(row.get("saves") or 0)
        topic = (row.get("topic") or "")[:38]
        if len(row.get("topic") or "") > 38:
            topic += "…"
        pub_date = (row.get("published_at") or "")[:10] or "—"
        asset_icon = "🎞" if row.get("asset_type") == "video" else "🖼"

        tbl.add_row(
            str(i),
            row.get("pillar") or "—",
            topic,
            pub_date,
            asset_icon,
            f"{reach:,}",
            f"{saves:,}",
            _save_pct(reach, saves),
        )
    return tbl


def _pillar_table(published_rows: list[dict]) -> Table:
    pillars: dict[str, dict] = {}
    for row in published_rows:
        p = row.get("pillar") or "Unknown"
        if p not in pillars:
            pillars[p] = {"posts": 0, "reach": 0, "saves": 0}
        pillars[p]["posts"] += 1
        pillars[p]["reach"] += int(row.get("reach") or 0)
        pillars[p]["saves"] += int(row.get("saves") or 0)

    tbl = Table(
        title="Engagement by Content Pillar",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold",
    )
    tbl.add_column("Pillar", style="bold", min_width=26, no_wrap=True)
    tbl.add_column("Posts", justify="right", width=6)
    tbl.add_column("Total Reach", justify="right", style="yellow", width=13)
    tbl.add_column("Total Saves", justify="right", style="magenta", width=13)
    tbl.add_column("Avg Reach/Post", justify="right", width=15)
    tbl.add_column("Save Rate", justify="right", width=10)

    for pillar, stats in sorted(
        pillars.items(), key=lambda x: x[1]["reach"], reverse=True
    ):
        avg = stats["reach"] // stats["posts"] if stats["posts"] else 0
        rate = (
            f"{stats['saves'] / stats['reach'] * 100:.2f}%"
            if stats["reach"]
            else "—"
        )
        tbl.add_row(
            pillar,
            str(stats["posts"]),
            f"{stats['reach']:,}",
            f"{stats['saves']:,}",
            f"{avg:,}",
            rate,
        )
    return tbl


def _cost_table(all_rows: list[dict]) -> Table:
    # TTS: count characters in scripts that were actually synthesised.
    # Primary signal: voice_path set (explicit evidence TTS ran).
    # Fallback: rows in qa_passed/published with a script definitely went
    # through TTS even if voice_path wasn't written back to the sheet.
    # SSML markup adds ~1.4× overhead — applied as a conservative estimate.
    tts_proxy_statuses = {"qa_passed", "published"}
    tts_row_ids = {
        row.get("id")
        for row in all_rows
        if row.get("voice_path") or row.get("status") in tts_proxy_statuses
    }
    raw_tts_chars = sum(
        len(row.get("script") or "")
        for row in all_rows
        if row.get("id") in tts_row_ids and row.get("script")
    )
    billed_tts_chars = int(raw_tts_chars * 1.4)  # SSML overhead estimate

    chargeable_tts = max(0, billed_tts_chars - _TTS_NEURAL2_FREE_CHARS)
    tts_cost_usd = chargeable_tts * _TTS_NEURAL2_RATE_PER_CHAR

    # Count image rows that definitely went through Pollinations.AI:
    # primary signal = images_folder_id set; fallback = any row with
    # asset_type=image that is in drafted/ready/qa_passed/published.
    _image_processed_statuses = {"drafted", "ready", "qa_passed", "published"}
    image_rows = sum(
        1
        for row in all_rows
        if row.get("asset_type") == "image"
        and (
            row.get("images_folder_id")
            or row.get("status") in _image_processed_statuses
        )
    )
    images_total = image_rows * _IMAGE_COUNT

    published_count = sum(
        1 for row in all_rows if row.get("status") == "published"
    )

    video_releases = sum(
        1
        for row in all_rows
        if (row.get("video_path") or "").startswith("https://github.com")
    )

    # Gemini: ~2 calls per content row + ~1 call per idea batch
    content_rows = sum(
        1
        for row in all_rows
        if row.get("status") != "idea" and row.get("caption")
    )
    idea_batches = max(
        1,
        len(all_rows) // int(os.environ.get("IDEA_BUFFER_SIZE", "10")),
    )
    gemini_est = content_rows * 2 + idea_batches

    total_cost = tts_cost_usd

    tbl = Table(
        title="Estimated Project Cost (USD)",
        box=box.ROUNDED,
        header_style="bold cyan",
        title_style="bold",
    )
    tbl.add_column("Service", style="bold", min_width=30)
    tbl.add_column("Usage", min_width=26)
    tbl.add_column("Free Tier", min_width=24)
    tbl.add_column("Cost", justify="right", min_width=10)

    service_rows = [
        (
            "Google Cloud TTS (Neural2-F)",
            f"{billed_tts_chars:,} chars (est.)\n"
            f"  raw: {raw_tts_chars:,} + SSML overhead",
            "100 K chars / month",
            f"${tts_cost_usd:.4f}",
        ),
        (
            "Gemini API (idea + content gen)",
            f"~{gemini_est} API calls (est.)",
            "1 500 req / day (free)",
            "$0.0000",
        ),
        (
            "Pollinations.AI (image gen)",
            f"{images_total} images ({image_rows} rows × {_IMAGE_COUNT})",
            "Unlimited, no key required",
            "$0.0000",
        ),
        (
            "Instagram Graph API",
            f"{published_count} posts published",
            "Free (Business account)",
            "$0.0000",
        ),
        (
            "GitHub (video hosting)",
            f"{video_releases} Release assets uploaded",
            "Free (public repo)",
            "$0.0000",
        ),
        (
            "Google Sheets / Drive / Auth",
            "Queue read/write + file storage",
            "Free tier",
            "$0.0000",
        ),
        (
            "YouTube Data API v3 (optional)",
            "Trending-video signal per run",
            "10 K units / day (free)",
            "$0.0000",
        ),
    ]

    for svc, usage, free, cost in service_rows:
        tbl.add_row(svc, usage, free, cost)

    tbl.add_section()
    cost_style = (
        "bold green" if total_cost < 1
        else "bold yellow" if total_cost < 10
        else "bold red"
    )
    tbl.add_row(
        "[bold]TOTAL[/bold]",
        "",
        "[dim]* TTS only; all other costs are $0[/dim]",
        f"[{cost_style}]${total_cost:.4f}[/{cost_style}]",
    )
    return tbl


# ─────────────────────────────────────────────────────────────────────────────
# Engagement summary panel
# ─────────────────────────────────────────────────────────────────────────────

def _summary_panel(published_rows: list[dict]) -> Panel:
    if not published_rows:
        return Panel(
            "[dim]No published posts yet.[/dim]",
            title=" Engagement Summary ",
            border_style="dim",
        )

    reaches = [int(r.get("reach") or 0) for r in published_rows]
    saves = [int(r.get("saves") or 0) for r in published_rows]

    total_reach = sum(reaches)
    total_saves = sum(saves)
    avg_reach = total_reach // len(published_rows)
    save_rate = (
        f"{total_saves / total_reach * 100:.2f}%" if total_reach else "—"
    )

    top_reach_row = max(published_rows, key=lambda r: int(r.get("reach") or 0))
    top_saves_row = max(published_rows, key=lambda r: int(r.get("saves") or 0))

    def _topic(row: dict) -> str:
        t = (row.get("topic") or "")[:38]
        return t + ("…" if len(row.get("topic") or "") > 38 else "")

    top_reach_val = int(top_reach_row.get("reach") or 0)
    top_saves_val = int(top_saves_row.get("saves") or 0)

    lines = [
        f"  [bold yellow]Total Reach[/bold yellow]       {total_reach:>10,}",
        f"  [bold magenta]Total Saves[/bold magenta]       {total_saves:>10,}",
        f"  [bold]Avg Reach / Post[/bold]  {avg_reach:>10,}",
        f"  [bold]Overall Save Rate[/bold] {save_rate:>10}",
        "",
        (
            f"  [bold yellow]Top Reach[/bold yellow]  "
            f"{top_reach_val:,}  —  {_topic(top_reach_row)}"
        ),
        (
            f"  [bold magenta]Top Saves[/bold magenta]  "
            f"{top_saves_val:,}  —  {_topic(top_saves_row)}"
        ),
    ]
    return Panel(
        "\n".join(lines),
        title=" Engagement Summary ",
        border_style="cyan",
        padding=(0, 1),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Instagram pipeline dashboard — engagement + cost summary."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Fetch fresh reach/saves from Instagram Insights API for every "
            "published post. Without this flag, metrics cached in the sheet "
            "are used (faster, no API quota consumed)."
        ),
    )
    args = parser.parse_args()

    console.print()
    console.rule("[bold blue] Instagram Pipeline Dashboard [/bold blue]")
    ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    console.print(f"  [dim]Generated: {ts}[/dim]\n")

    with console.status("[cyan]Loading rows from Google Sheet…[/cyan]"):
        try:
            all_rows = sheets_client.get_all_rows()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]ERROR: could not load sheet — {exc}[/red]")
            sys.exit(1)

    console.print(f"  Loaded [bold]{len(all_rows)}[/bold] rows from queue.\n")

    published_rows = [r for r in all_rows if r.get("status") == "published"]

    if args.refresh:
        if published_rows:
            console.print(
                f"  [cyan]Refreshing Instagram Insights for "
                f"{len(published_rows)} published posts…[/cyan]"
            )
            _refresh_insights(published_rows)
            console.print()
        else:
            console.print(
                "  [dim]No published posts — --refresh skipped.[/dim]\n"
            )

    # 1. Pipeline status
    console.print(_pipeline_status_table(all_rows))
    console.print()

    # 2. Per-post engagement
    if published_rows:
        console.print(_engagement_table(published_rows))
        console.print()

        # 3. By pillar
        console.print(_pillar_table(published_rows))
        console.print()

        # 4. Summary panel
        console.print(_summary_panel(published_rows))
        console.print()
    else:
        console.print(
            Panel(
                "[dim]No published posts yet — engagement metrics will appear "
                "after your first publish.[/dim]",
                title=" Engagement ",
                border_style="dim",
            )
        )
        console.print()

    # 5. Cost breakdown
    console.print(_cost_table(all_rows))
    console.print()

    console.rule("[dim]End of Dashboard[/dim]")
    console.print()


if __name__ == "__main__":
    main()
