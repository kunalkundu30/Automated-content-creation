# Background music

`assemble_video.py` picks a random `.mp3` from this folder for each video's
background track. This folder is intentionally empty in the repo — music
licensing means real tracks can't be bundled here for you.

This is a one-time task, not a recurring one: download 5-10 tracks you like
from a source that explicitly grants commercial-use rights (Pixabay Music is
a good free option — check the license on each track, since terms are
sometimes set per-track rather than site-wide) and drop the mp3 files
directly in this folder. Commit them to the repo (or, if you'd rather not
commit binary files, adjust `_pick_music_track()` in assemble_video.py to
pull from a Drive folder instead, the same way images are downloaded).
