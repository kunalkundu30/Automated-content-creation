"""Stage: drafted (with visual assets ready) -> ready

Assembles a finished reel from visual assets in a per-row Drive folder, plus
the AI voiceover and its caption timing. The folder can hold static images
(Canva/Leonardo, as before) and/or short AI-generated video clips — either
way, this stage turns already-made visuals into a captioned, scored, finished
video file.

Requires ffmpeg on PATH — already present on GitHub Actions' ubuntu-latest
runners, or `sudo apt-get install -y ffmpeg` anywhere else.
"""
import glob
import os
import random
import shutil
import subprocess
import uuid

from PIL import Image, ImageDraw, ImageFont

import drive_client
import github_storage

_MUSIC_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "music")
_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
_WIDTH, _HEIGHT, _FPS = 1080, 1920, 30
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm")


def build_video(
    images_folder_id: str,
    voice_audio_path: str,
    caption_chunks: list[dict],
    row_id: str,
) -> str:
    """Assembles the video, uploads it to a GitHub Release for public access,
    and returns the permanent browser_download_url. A local copy is kept in
    ig-ai-pipeline/output/ for inspection.

    `images_folder_id` may be either:
      - A local directory path (from generate_images.generate_images_locally)
        — files are used directly and the directory is deleted after assembly.
      - A Google Drive folder ID (for video rows where the user uploads clips)
        — files are downloaded from Drive.
    """
    os.makedirs(_OUTPUT_DIR, exist_ok=True)
    work_dir = f"/tmp/video_{row_id}_{uuid.uuid4().hex[:8]}"
    os.makedirs(work_dir, exist_ok=True)
    local_images_dir = images_folder_id if os.path.isdir(images_folder_id) else None

    try:
        if local_images_dir:
            # Images already on disk — use them directly (sorted for slideshow order)
            all_files = sorted(os.listdir(local_images_dir))
            assets = [
                os.path.join(local_images_dir, f)
                for f in all_files
                if not f.startswith(".")
            ]
        else:
            assets = drive_client.download_folder_files(
                images_folder_id, f"{work_dir}/assets"
            )
        if not assets:
            raise RuntimeError(
                f"No visual assets found in {images_folder_id}"
            )

        total_duration = _get_duration(voice_audio_path)
        segments = _build_segments(assets, total_duration, work_dir)
        slideshow = _concat_segments(segments, work_dir, total_duration)
        captioned = _burn_captions(slideshow, caption_chunks, work_dir)
        music_track = _pick_music_track()
        if music_track:
            audio = _mix_audio(
                voice_audio_path, music_track, total_duration, work_dir
            )
        else:
            audio = voice_audio_path

        filename = f"reel_{row_id}.mp4"
        final_path = os.path.join(_OUTPUT_DIR, filename)
        _mux_final(captioned, audio, final_path)
        return github_storage.upload_video(final_path, filename)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        if local_images_dir:
            shutil.rmtree(local_images_dir, ignore_errors=True)


def _get_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _is_video(path: str) -> bool:
    return path.lower().endswith(_VIDEO_EXTENSIONS)


def _build_segments(
    assets: list[str], total_duration: float, work_dir: str
) -> list[str]:
    """Normalizes every asset to the same resolution/fps/pixel format so they
    concatenate cleanly. Video clips play at their own native duration;
    whatever time is left over is split evenly across the still images.
    """
    images = [a for a in assets if not _is_video(a)]
    video_clips = [a for a in assets if _is_video(a)]

    time_used_by_clips = sum(_get_duration(v) for v in video_clips)
    remaining = max(
        total_duration - time_used_by_clips, 1.0 if images else 0.0
    )
    per_image_duration = remaining / len(images) if images else 0.0

    segment_paths = []
    for i, asset in enumerate(assets):
        seg_path = f"{work_dir}/segment_{i}.mp4"
        if _is_video(asset):
            subprocess.run(
                ["ffmpeg", "-y", "-i", asset, "-vf",
                 f"scale={_WIDTH}:{_HEIGHT}"
                 ":force_original_aspect_ratio=increase,"
                 f"crop={_WIDTH}:{_HEIGHT}",
                 "-r", str(_FPS), "-an", "-pix_fmt", "yuv420p", seg_path],
                check=True, capture_output=True,
            )
        else:
            frames = max(1, int(per_image_duration * _FPS))
            zoompan = (
                f"zoompan=z='min(zoom+0.0015,1.2)':d={frames}"
                f":s={_WIDTH}x{_HEIGHT}:fps={_FPS}"
            )
            subprocess.run(
                ["ffmpeg", "-y", "-loop", "1", "-i", asset,
                 "-vf", zoompan, "-t", str(per_image_duration),
                 "-pix_fmt", "yuv420p", seg_path],
                check=True, capture_output=True,
            )
        segment_paths.append(seg_path)
    return segment_paths


def _concat_segments(
    segment_paths: list[str], work_dir: str, target_duration: float
) -> str:
    list_path = f"{work_dir}/concat_list.txt"
    with open(list_path, "w") as f:
        for p in segment_paths:
            f.write(f"file '{p}'\n")
    raw_path = f"{work_dir}/slideshow_raw.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_path, "-c", "copy", raw_path],
        check=True, capture_output=True,
    )

    if _get_duration(raw_path) > target_duration + 1.0:
        trimmed_path = f"{work_dir}/slideshow.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-i", raw_path,
             "-t", str(target_duration), "-c", "copy", trimmed_path],
            check=True, capture_output=True,
        )
        return trimmed_path
    return raw_path


_FONT_PATHS = [
    "/Library/Fonts/Arial Unicode.ttf",       # macOS
    "/Library/Fonts/Arial.ttf",               # macOS (with Office)
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",  # Ubuntu
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",                  # Ubuntu
]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


_CAPTION_SIDE_MARGIN = 60   # px kept clear on each side of the frame
_CAPTION_BOX_PAD = 20       # px between text and its background box


def _wrap_text(text: str, draw: ImageDraw.ImageDraw,
               font: ImageFont.FreeTypeFont, max_width: int) -> str:
    """Word-wraps text so no line exceeds max_width pixels. Returns the
    wrapped string with newlines inserted at word boundaries.
    """
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        test = " ".join(current + [word])
        w = draw.textbbox((0, 0), test, font=font)[2]
        if w <= max_width or not current:
            # Fits on current line, or it's a single word wider than the frame
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _make_caption_image(text: str, out_path: str) -> None:
    """Renders white text with a semi-transparent black box onto a transparent
    canvas the same size as the output frame, then saves it as RGBA PNG.
    Text is word-wrapped to stay within safe margins so it never bleeds past
    the video edges.
    """
    img = Image.new("RGBA", (_WIDTH, _HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _load_font(64)

    max_text_width = _WIDTH - (_CAPTION_SIDE_MARGIN + _CAPTION_BOX_PAD) * 2
    wrapped = _wrap_text(text, draw, font, max_text_width)

    bbox = draw.multiline_textbbox(
        (0, 0), wrapped, font=font, spacing=8, align="center"
    )
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (_WIDTH - tw) // 2
    y = _HEIGHT - 400 - th

    draw.rectangle(
        [x - _CAPTION_BOX_PAD, y - _CAPTION_BOX_PAD,
         x + tw + _CAPTION_BOX_PAD, y + th + _CAPTION_BOX_PAD],
        fill=(0, 0, 0, 128),
    )
    draw.multiline_text(
        (x, y), wrapped, font=font, fill="white", spacing=8, align="center"
    )
    img.save(out_path, "PNG")


def _burn_captions(
    slideshow_path: str, caption_chunks: list[dict], work_dir: str
) -> str:
    """Overlays caption images onto the video using ffmpeg's overlay filter.
    Uses Pillow for text rendering so no libfreetype build of ffmpeg is needed.
    """
    overlay_paths = []
    for i, chunk in enumerate(caption_chunks):
        p = f"{work_dir}/cap_{i}.png"
        _make_caption_image(chunk["text"], p)
        overlay_paths.append(p)

    captioned_path = f"{work_dir}/captioned.mp4"
    cmd = ["ffmpeg", "-y", "-i", slideshow_path]
    for path in overlay_paths:
        cmd += ["-i", path]

    # Chain: [0:v][1:v]overlay=...[cap0]; [cap0][2:v]overlay=...[cap1]; ...
    filter_parts = []
    for i, chunk in enumerate(caption_chunks):
        src = "0:v" if i == 0 else f"cap{i - 1}"
        filter_parts.append(
            f"[{src}][{i + 1}:v]overlay=0:0:"
            f"enable='between(t,{chunk['start']},{chunk['end']})'[cap{i}]"
        )

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[cap{len(caption_chunks) - 1}]",
        captioned_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return captioned_path


def _pick_music_track() -> str | None:
    """Returns a random track path, or None if the music folder is empty."""
    tracks = glob.glob(f"{_MUSIC_DIR}/*.mp3")
    return random.choice(tracks) if tracks else None


def _mix_audio(
    voice_path: str, music_path: str, total_duration: float, work_dir: str
) -> str:
    mixed_path = f"{work_dir}/mixed_audio.aac"
    filter_complex = (
        f"[1:a]volume=0.15,atrim=0:{total_duration}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", voice_path, "-i", music_path,
         "-filter_complex", filter_complex, "-map", "[aout]", mixed_path],
        check=True, capture_output=True,
    )
    return mixed_path


def _mux_final(
    captioned_video: str, mixed_audio: str, output_path: str
) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", captioned_video, "-i", mixed_audio,
         "-map", "0:v", "-map", "1:a", "-c:v", "copy",
         "-shortest", output_path],
        check=True, capture_output=True,
    )
