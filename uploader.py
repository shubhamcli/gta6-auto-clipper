import argparse
from datetime import datetime, timedelta, timezone
import json
import os
import re
import sys
import time
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ================= DEFAULT CONFIG =================
DEFAULT_JSON_FILE = "techno_gamerz_gta6_ep1_clips.json"
DEFAULT_CLIPS_DIR = "final_clips_pro"
CLIENT_SECRETS_FILE = "client_secrets.json"
TOKEN_FILE = "token.json"
UPLOAD_LOG_FILE = "uploaded_log.json"

DEFAULT_MAX_UPLOADS = 5     # Maximum of 5 videos uploaded per run
PRIVACY_STATUS = "private"  # Fallback status when publish_at is absent ("private" | "public" | "unlisted")
CATEGORY_ID = "20"          # 20 = Gaming
IST = timezone(timedelta(hours=5, minutes=30))
# ==================================================

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_authenticated_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRETS_FILE):
                print(f"Error: '{CLIENT_SECRETS_FILE}' was not found in this folder.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def load_upload_log() -> dict:
    if os.path.exists(UPLOAD_LOG_FILE):
        with open(UPLOAD_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_upload_log(log: dict):
    with open(UPLOAD_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def find_clip_file(clip: dict, clips_dir: str) -> str | None:
    num = clip["clip_number"]
    safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", clip["title"]).strip("_")[:45]
    expected = os.path.join(clips_dir, f"clip_{num}_{safe_title}_PRO.mp4")
    if os.path.exists(expected):
        return expected

    # Fallback: if the filename differs slightly, find clip_X_..._PRO.mp4
    if os.path.isdir(clips_dir):
        prefix = f"clip_{num}_"
        for fname in os.listdir(clips_dir):
            if fname.startswith(prefix) and fname.endswith("_PRO.mp4"):
                return os.path.join(clips_dir, fname)
    return None


def parse_publish_at(raw_time: str | None) -> str | None:
    """
    Convert the JSON 'publish_at' value into YouTube API RFC 3339 format (IST +05:30).
    Supported formats:
      - "2026-11-22 21:00"
      - "2026-11-22 09:00 PM"
      - "2026-11-22 21:00:00"
      - "22-11-2026 21:00"
      - "2026-11-22T21:00:00+05:30" (ISO 8601)
    Return None if the time is in the past or invalid, allowing a safe private fallback.
    """
    if not raw_time or not str(raw_time).strip():
        return None

    val = str(raw_time).strip()
    dt = None

    # 1. Direct ISO 8601 check (e.g. 2026-11-22T21:00:00+05:30 or 2026-11-22T15:30:00Z)
    try:
        iso_val = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso_val)
    except ValueError:
        pass

    # 2. Common human-friendly date/time formats
    if dt is None:
        formats = [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %I:%M %p",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y %I:%M %p",
            "%d/%m/%Y %H:%M",
            "%d/%m/%Y %I:%M %p",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(val, fmt)
                break
            except ValueError:
                continue

    if dt is None:
        print(f"[Schedule Warning] Invalid publish_at format '{val}' -> Uploading as private.")
        return None

    # If no timezone is provided, default to Indian Standard Time (IST +05:30)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)

    # YouTube rejects scheduled times in the past or too close to now (<= 2 minutes)
    min_allowed = datetime.now(IST) + timedelta(minutes=2)
    if dt <= min_allowed:
        print(f"[Schedule Warning] '{val}' is in the past or less than 2 minutes away -> Uploading as private.")
        return None

    return dt.isoformat(timespec="seconds")


def upload_video(
    youtube,
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = PRIVACY_STATUS,
    publish_at: str | None = None,
):
    status_body = {
        "privacyStatus": "private" if publish_at else privacy_status,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        # YouTube Data API requires privacyStatus='private' and publishAt when scheduling.
        status_body["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": CATEGORY_ID,
        },
        "status": status_body,
    }

    media = MediaFileUpload(file_path, mimetype="video/mp4", resumable=True, chunksize=-1)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Uploading... {int(status.progress() * 100)}%")

    return response.get("id")


def parse_args():
    parser = argparse.ArgumentParser(description="YouTube Shorts Auto Uploader")
    parser.add_argument("--json", dest="json_file", default=DEFAULT_JSON_FILE, help="Path to clips JSON")
    parser.add_argument("--clips-dir", dest="clips_dir", default=DEFAULT_CLIPS_DIR, help="Directory with _PRO.mp4 clips")
    parser.add_argument("--max-uploads", dest="max_uploads", type=int, default=DEFAULT_MAX_UPLOADS)
    parser.add_argument(
        "--privacy",
        dest="privacy",
        default=PRIVACY_STATUS,
        choices=["private", "public", "unlisted"],
        help="Default privacy when publish_at is absent from the JSON",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    json_file = args.json_file
    clips_dir = args.clips_dir
    max_uploads = args.max_uploads

    if not os.path.exists(json_file):
        print(f"Error: '{json_file}' was not found.")
        sys.exit(1)

    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data.get("video_metadata", {})
    channel_name = meta.get("channel", "Original Creator")
    channel_tag = re.sub(r"[^a-zA-Z0-9]", "", channel_name)
    source_url = meta.get("url", "")
    clips = data.get("clips", [])

    upload_log = load_upload_log()
    youtube = get_authenticated_service()

    uploaded_count = 0
    failed_count = 0

    for clip in clips:
        if uploaded_count >= max_uploads:
            print(f"\nMaximum limit ({max_uploads}) reached. Stopping.")
            break

        video_path = find_clip_file(clip, clips_dir)
        if not video_path:
            print(f"[Skip] Clip {clip['clip_number']} file was not found in '{clips_dir}'.")
            continue

        norm_key = os.path.normpath(video_path)
        if norm_key in upload_log or video_path in upload_log:
            yt_prev = upload_log.get(norm_key) or upload_log.get(video_path)
            print(f"[Skip] Already uploaded: {video_path} (YT ID: {yt_prev})")
            continue

        title = f"{clip['title']} #shorts #gta6"
        creator_hashtag = f" #{channel_tag}" if channel_tag else ""
        description = (
            f"{clip.get('hook_idea', clip['title'])}\n\n"
            f"Credit: {channel_name}\n"
            f"Original Video: {source_url}\n\n"
            f"#gta6 #gta6gameplay #gtavi #shorts #gamingshorts{creator_hashtag}"
        )
        tags = ["GTA 6", "GTA VI", "GTA 6 Gameplay", "Shorts", "Gaming", channel_name]

        # Read per-clip schedule time (publish_at) and optional privacy_status from JSON
        publish_at_iso = parse_publish_at(clip.get("publish_at"))
        clip_privacy = (clip.get("privacy_status") or args.privacy).strip().lower()
        if clip_privacy not in ("private", "public", "unlisted"):
            clip_privacy = args.privacy

        status_label = f"Scheduled: {publish_at_iso}" if publish_at_iso else f"Status: {clip_privacy}"
        print(f"\n--- Uploading [{uploaded_count + 1}/{max_uploads}]: {title} ({status_label}) ---")
        try:
            yt_id = upload_video(
                youtube,
                video_path,
                title,
                description,
                tags,
                privacy_status=clip_privacy,
                publish_at=publish_at_iso,
            )
            print(f"Success! Video URL: https://youtube.com/shorts/{yt_id} ({status_label})")

            upload_log[norm_key] = yt_id
            save_upload_log(upload_log)
            uploaded_count += 1

            # Wait 3 seconds before the next upload
            if uploaded_count < max_uploads:
                time.sleep(3)

        except Exception as e:
            failed_count += 1
            print(f"Upload failed for Clip {clip['clip_number']}: {e}")

    print(f"\nDone! Total videos uploaded: {uploaded_count}. Failed: {failed_count}.")

    # If any upload fails, return a non-zero exit code so server.py does not delete the folder.
    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()