"""Entry point. Tops up the idea queue, then reads every row and moves it
one step forward based on its current status. Safe to run repeatedly (e.g.
every few hours from GitHub Actions) — each stage only touches rows already
in the exact status it expects, so a row never gets processed twice the same
way.

Row lifecycle:
    idea -> drafted -> [drafted, waiting on assets] -> ready -> qa_passed
    -> published

Manual steps: fill in asset_type ("image" or "video") to trigger prompt
generation, then create assets using that prompt, upload to Drive, and paste
the folder ID into images_folder_id. Everything else is automatic.
"""
import os
import uuid
from datetime import date

import config
import github_storage
import sheets_client
from pipeline import (
    analytics,
    generate_content,
    generate_ideas,
    generate_voice,
    assemble_video,
    publish_instagram,
    qa_checks,
)


def run() -> None:
    rows = sheets_client.get_all_rows()
    print(f"Loaded {len(rows)} rows from the queue.")

    _top_up_ideas(rows)

    for row in rows:
        status = row.get("status", "")
        row_num = row["_sheet_row_number"]

        try:
            if status == "idea":
                _handle_idea(row, row_num)
            elif status == "drafted":
                _handle_drafted(row, row_num)
            elif status == "ready":
                _handle_ready(row, row_num)
            elif status == "qa_passed":
                _handle_qa_passed(row, row_num)
            elif status == "published":
                _handle_published(row, row_num)
        except Exception as exc:  # noqa: BLE001 — log and keep going
            print(f"Row {row_num} ({status}) failed: {exc}")
            sheets_client.update_row(row_num, {"qa_notes": f"ERROR: {exc}"})


def _top_up_ideas(rows: list[dict]) -> None:
    """Keeps the queue stocked so the pipeline never runs dry. Counts every
    row not yet published as "in the pipeline"; tops up to
    config.IDEA_BUFFER_SIZE.
    """
    in_pipeline = [
        r for r in rows if r.get("status") not in ("", "published")
    ]
    shortfall = config.IDEA_BUFFER_SIZE - len(in_pipeline)
    if shortfall <= 0:
        return

    recent_topics = [r["topic"] for r in rows[-50:] if r.get("topic")]
    new_ideas = generate_ideas.generate_ideas(shortfall, recent_topics)
    if not new_ideas:
        print("Idea generation returned nothing — leaving queue as-is.")
        return

    new_rows = [
        {"id": uuid.uuid4().hex[:8], "status": "idea", **idea}
        for idea in new_ideas
    ]
    sheets_client.append_rows(new_rows)
    print(
        f"Queue was {len(in_pipeline)}/{config.IDEA_BUFFER_SIZE}"
        f" — added {len(new_rows)} new ideas."
    )


def _handle_idea(row: dict, row_num: int) -> None:
    result = generate_content.generate_caption_and_script(
        row["topic"], row["pillar"]
    )
    sheets_client.update_row(row_num, {
        "caption": result["caption"],
        "script": result["script"],
        "status": "drafted",
    })
    print(f"Row {row_num}: drafted caption + script.")


def _handle_drafted(row: dict, row_num: int) -> None:
    # Auto-generate asset prompt as soon as user fills in asset_type
    asset_type = row.get("asset_type", "").strip().lower()
    if asset_type and not row.get("asset_prompt"):
        asset_prompt = generate_content.generate_asset_prompt(
            topic=row["topic"],
            pillar=row["pillar"],
            caption=row["caption"],
            script=row["script"],
            asset_type=asset_type,
        )
        sheets_client.update_row(row_num, {"asset_prompt": asset_prompt})
        print(f"Row {row_num}: generated {asset_type} prompt.")

    if not row.get("images_folder_id"):
        return  # waiting on assets folder ID
    if row.get("video_path"):
        return  # already assembled, leave it

    voice_result = generate_voice.generate_voiceover_with_timing(
        row["script"], row["id"]
    )
    video_url = assemble_video.build_video(
        images_folder_id=row["images_folder_id"],
        voice_audio_path=voice_result["local_audio_path"],
        caption_chunks=voice_result["caption_chunks"],
        row_id=row["id"],
    )
    sheets_client.update_row(row_num, {
        "video_path": video_url,
        "status": "ready",
    })
    print(f"Row {row_num}: voice + video assembled, now ready for QA.")


def _handle_ready(row: dict, row_num: int) -> None:
    passed, reason = qa_checks.run_qa(row)
    if passed:
        sheets_client.update_row(
            row_num, {"status": "qa_passed", "qa_notes": ""}
        )
        print(f"Row {row_num}: passed QA.")
    else:
        sheets_client.update_row(
            row_num, {"status": "qa_failed", "qa_notes": reason}
        )
        print(f"Row {row_num}: failed QA — {reason}")


def _handle_qa_passed(row: dict, row_num: int) -> None:
    scheduled = row.get("scheduled_date", "")
    if scheduled > date.today().isoformat():
        return  # not due yet

    video_path = row["video_path"]
    # video_path may be a local file (assembled before GitHub storage was
    # added) — upload it now so Instagram gets a public URL
    if not video_path.startswith("http") and os.path.isfile(video_path):
        filename = os.path.basename(video_path)
        video_path = github_storage.upload_video(video_path, filename)
        sheets_client.update_row(row_num, {"video_path": video_path})

    media_id = publish_instagram.publish_reel(video_path, row["caption"])
    sheets_client.update_row(row_num, {
        "status": "published",
        "media_id": media_id,
        "published_at": date.today().isoformat(),
    })
    print(f"Row {row_num}: published as media {media_id}.")


def _handle_published(row: dict, row_num: int) -> None:
    if not row.get("media_id") or row.get("reach"):
        return  # nothing to fetch, or already logged
    metrics = analytics.fetch_insights(row["media_id"])
    sheets_client.update_row(row_num, {
        "reach": metrics["reach"],
        "saves": metrics["saves"],
    })
    print(
        f"Row {row_num}: logged {metrics['reach']} reach"
        f" / {metrics['saves']} saves."
    )


if __name__ == "__main__":
    run()
