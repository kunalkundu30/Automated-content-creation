"""Shared Drive helpers used by both the voice and video stages — upload a
local file and get a link back, or pull every file out of a folder.
"""
import os

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2 import service_account

import config

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_creds = service_account.Credentials.from_service_account_file(
    config.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_SCOPES
)
_service = build("drive", "v3", credentials=_creds)


def upload_file(local_path: str, filename: str, folder_id: str, make_public: bool = False) -> dict:
    """Uploads a file to Drive. Returns {"id", "web_view_link", "direct_url"}.
    `direct_url` is only populated when make_public=True — it's a raw-bytes
    link (not the HTML viewer page), which is what an external API like
    Instagram's needs to actually fetch the file. Google Drive shows a
    virus-scan interstitial instead of raw bytes above roughly 100MB, so this
    only works reliably for short, low-resolution reels like these — a
    genuinely large-file use case would need a different free host.
    """
    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(local_path)
    uploaded = _service.files().create(
        body=file_metadata, media_body=media, fields="id, webViewLink"
    ).execute()

    result = {"id": uploaded["id"], "web_view_link": uploaded["webViewLink"], "direct_url": None}

    if make_public:
        _service.permissions().create(
            fileId=uploaded["id"], body={"role": "reader", "type": "anyone"}
        ).execute()
        result["direct_url"] = f"https://drive.google.com/uc?export=download&id={uploaded['id']}"

    return result


def download_folder_files(folder_id: str, dest_dir: str) -> list[str]:
    """Downloads every file in a Drive folder to dest_dir, sorted by name
    (name your images 01.jpg, 02.jpg, ... so the slideshow order is
    predictable). Returns the local paths in that same order.
    """
    os.makedirs(dest_dir, exist_ok=True)
    query = f"'{folder_id}' in parents and trashed = false"
    response = _service.files().list(q=query, fields="files(id, name)").execute()
    files = sorted(response.get("files", []), key=lambda f: f["name"])

    local_paths = []
    for file in files:
        local_path = os.path.join(dest_dir, file["name"])
        request = _service.files().get_media(fileId=file["id"])
        with open(local_path, "wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        local_paths.append(local_path)

    return local_paths
