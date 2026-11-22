# GTA 6 Auto Clipper

A Windows-oriented web dashboard and command-line toolkit that:

- downloads YouTube Shorts clips;
- creates 1:1, 4:3, or 3:4 vertical layouts;
- optionally adds a selected facecam area;
- synchronizes animated word captions from YouTube transcripts;
- optionally uploads completed clips to YouTube with scheduling support.

This project is intended for authorized use with videos you own or have permission to download and edit.

## Features

- FastAPI dashboard with JSON upload and live pipeline status;
- visual facecam crop selection;
- 9:16 output with centered captions;
- automatic YouTube language detection;
- Hindi-to-Romanized-Hindi caption conversion;
- configurable privacy and scheduled publishing;
- duplicate-upload protection through `uploaded_log.json`;
- safe cleanup after successful YouTube uploads.

## Project Layout

| File | Purpose |
| --- | --- |
| `server.py` | FastAPI dashboard and pipeline worker |
| `index.html` | Browser dashboard |
| `clipper.py` | Downloads and renders clips |
| `uploader.py` | Authenticates with YouTube and uploads clips |
| `prompt.txt` | Example clip-generation prompt |
| `jsonme.json` | Example clip-list JSON |
| `requirements.txt` | Python dependencies |

Generated directories such as `final_clips_pro/`, `dashboard_jobs/`, and `venv/` are excluded by `.gitignore`.

## Requirements

- Windows 10 or 11;
- Python 3.10 or newer;
- FFmpeg available on `PATH`;
- `yt-dlp` available on `PATH` or installed through Python;
- a Google account;
- a YouTube Data API-enabled Google Cloud project for uploads.

Verify the external tools:

```powershell
python --version
ffmpeg -version
yt-dlp --version
```

FFmpeg and yt-dlp can be installed on Windows with:

```powershell
winget install Gyan.FFmpeg
winget install yt-dlp.yt-dlp
```

## Installation

Open PowerShell in the project directory.

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If script execution is disabled, activate the environment with Command Prompt:

```powershell
venv\Scripts\activate.bat
```

## YouTube API Setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project.
3. Open **APIs & Services > Library**.
4. Search for **YouTube Data API v3** and enable it.
5. Open **APIs & Services > OAuth consent screen**.
6. Choose **External** or **Internal**, depending on the account and organization.
7. Add your Google account as a test user while the app is in testing mode.
8. Open **APIs & Services > Credentials**.
9. Select **Create Credentials > OAuth client ID**.
10. Choose **Desktop app**.
11. Download the OAuth JSON file.
12. Rename the downloaded file to `client_secrets.json` and place it in the project root.

The first YouTube upload opens a browser for authorization. The resulting token is saved to `token.json`. Both `client_secrets.json` and `token.json` are ignored by Git and must never be committed.

## Clip JSON Format

Create a JSON file like this:

```json
{
  "video_metadata": {
    "title": "Example YouTube Video",
    "channel": "Example Channel",
    "url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "video_id": "VIDEO_ID"
  },
  "clips": [
    {
      "clip_number": 1,
      "title": "Example Clip Title",
      "hook_idea": "A SHORT, SCROLL-STOPPING HOOK",
      "start_time": "00:01:22",
      "end_time": "00:02:17",
      "publish_at": "2026-11-22 21:00",
      "privacy_status": "private"
    }
  ]
}
```

### Supported fields

- `video_metadata.url`: source YouTube URL.
- `video_metadata.video_id`: YouTube video ID.
- `clip_number`: unique clip number used in generated filenames.
- `start_time` and `end_time`: timestamps in `HH:MM:SS` format.
- `hook_idea`: optional hook displayed by `--show-hook`.
- `publish_at`: optional schedule in RFC 3339 or a supported local date-time format.
- `privacy_status`: `private`, `public`, or `unlisted`.

Scheduled uploads require privacy status `private` internally until YouTube processes the scheduled publication.

## Run the Dashboard

```powershell
uvicorn server:app --host 127.0.0.1 --port 8000 --reload
```

Open <http://127.0.0.1:8000>.

### Dashboard workflow

1. Upload a clip JSON file.
2. Wait for the preview frame.
3. Optionally drag over the facecam area.
4. Choose `No facecam` for a centered 1:1 layout.
5. Enable `Show hook text` if needed.
6. Choose whether to upload automatically.
7. Start rendering and follow the live status.

Successful automatic uploads remove the output folder, temporary job JSON, and preview for that video. If rendering or uploading fails, cleanup is skipped so files remain available.

## Run the Clipper from the CLI

The clipper automatically finds a supported local JSON file when `--json` is omitted.

```powershell
python clipper.py --json jsonme.json --output-dir final_clips_pro
```

Common options:

```powershell
python clipper.py --json jsonme.json --aspect 1:1
python clipper.py --json jsonme.json --aspect 3:4 --show-hook
python clipper.py --json jsonme.json --facecam "0.70,0.10,0.25,0.25"
```

Facecam coordinates are normalized:

- `fx`: horizontal position;
- `fy`: vertical position;
- `fw`: crop width;
- `fh`: crop height.

Each value ranges from `0.0` to `1.0`.

## Run the Uploader from the CLI

```powershell
python uploader.py --json jsonme.json --clips-dir final_clips_pro
```

Options:

```powershell
python uploader.py --json jsonme.json --clips-dir final_clips_pro --max-uploads 5
python uploader.py --json jsonme.json --clips-dir final_clips_pro --privacy unlisted
```

The uploader creates or refreshes `token.json`, tracks uploaded files in `uploaded_log.json`, and uses private privacy as the fallback.

## Development

Compile the Python files:

```powershell
python -m py_compile clipper.py server.py uploader.py
```

Before publishing changes:

1. Remove `client_secrets.json` and `token.json`.
2. Check that no API keys or OAuth credentials appear in source files.
3. Delete generated clips and dashboard jobs.
4. Compile Python files and test both dashboard and CLI paths.

## Troubleshooting

### `yt-dlp` or `ffmpeg` was not found

Confirm the tools are installed and restart PowerShell after installation:

```powershell
where.exe yt-dlp
where.exe ffmpeg
```

### Google authorization fails

- verify YouTube Data API v3 is enabled;
- use the OAuth Desktop app credential type;
- add the account to OAuth test users;
- delete `token.json` and retry authorization if the previous token is invalid.

### The output file is not created

- confirm the YouTube URL is accessible;
- confirm subtitles or automatic captions are available;
- check the start and end timestamps;
- inspect the terminal output for `ffmpeg` errors.

### A clip is skipped during upload

The uploader skips clips already recorded in `uploaded_log.json` or clips whose final `_PRO.mp4` filename cannot be found.

## Security and Privacy

- Never commit OAuth secrets or access tokens.
- Never share `client_secrets.json` or `token.json`.
- If a secret is exposed, revoke it in Google Cloud and create a new OAuth client.
- Be careful with source videos: obtain permission and follow YouTube's terms and applicable copyright rules.
