import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ================= CONFIG =================
JOBS_DIR = "dashboard_jobs"
PREVIEWS_DIR = os.path.join(JOBS_DIR, "previews")
BASE_OUTPUT_DIR = "final_clips_pro"
# ==========================================

os.makedirs(JOBS_DIR, exist_ok=True)
os.makedirs(PREVIEWS_DIR, exist_ok=True)
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)

app = FastAPI(title="GTA 6 Auto Clipper & Uploader Dashboard")
app.mount("/previews", StaticFiles(directory=PREVIEWS_DIR), name="previews")

state_lock = threading.Lock()
pipeline_state = {
    "running": False,
    "status": "idle",  # idle | running | completed | error
    "step": "Waiting for JSON upload...",
    "logs": [],
}


def append_log(line: str):
    with state_lock:
        pipeline_state["logs"].append(line)
        if len(pipeline_state["logs"]) > 500:
            pipeline_state["logs"] = pipeline_state["logs"][-500:]


def extract_preview_frame(video_url: str, timestamp: str, out_jpg: str) -> bool:
    """Extract one preview frame at the exact timestamp without downloading the full YouTube video."""
    try:
        yt_cmd = [
            "yt-dlp",
            "-f", "bestvideo[height<=720][ext=mp4]/best[height<=720]/best",
            "-g",
            video_url,
        ]
        res = subprocess.run(yt_cmd, capture_output=True, text=True, check=True, timeout=45)
        stream_urls = [u.strip() for u in res.stdout.splitlines() if u.strip()]
        if not stream_urls:
            return False

        stream_url = stream_urls[0]
        ff_cmd = [
            "ffmpeg", "-y",
            "-ss", timestamp,
            "-i", stream_url,
            "-frames:v", "1",
            "-q:v", "2",
            out_jpg,
        ]
        subprocess.run(ff_cmd, capture_output=True, check=True, timeout=45)
        return os.path.exists(out_jpg)
    except Exception as e:
        print(f"Preview extraction failed: {e}")
        return False


def run_command_streamed(cmd: list[str], step_label: str) -> int:
    with state_lock:
        pipeline_state["step"] = step_label
    append_log(f"\n>>> {step_label}")
    append_log(f"$ {' '.join(cmd)}\n")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        append_log(line.rstrip("\r\n"))

    return process.wait()


def cleanup_job_files(output_dir: str, json_path: str, video_id: str):
    """Clean the output folder, preview, and temporary JSON after a successful YouTube upload."""
    append_log("\n>>> Step 3/3: Cleaning up temporary folders & rendered files...")

    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir, ignore_errors=True)
        append_log(f"Cleaned folder: {output_dir}")

    if json_path and os.path.exists(json_path):
        try:
            os.remove(json_path)
            append_log(f"Cleaned job JSON: {json_path}")
        except OSError:
            pass

    preview_path = os.path.join(PREVIEWS_DIR, f"{video_id}.jpg")
    if os.path.exists(preview_path):
        try:
            os.remove(preview_path)
            append_log(f"Cleaned preview: {preview_path}")
        except OSError:
            pass


class StartRequest(BaseModel):
    json_path: str
    video_id: str
    facecam: Optional[str] = None  # "fx,fy,fw,fh" or None
    aspect: str = "1:1"
    show_hook: bool = False
    auto_upload: bool = True


def worker_pipeline(req: StartRequest):
    try:
        output_dir = os.path.join(BASE_OUTPUT_DIR, req.video_id)
        os.makedirs(output_dir, exist_ok=True)

        clipper_cmd = [
            sys.executable, "-u", "clipper.py",
            "--json", req.json_path,
            "--output-dir", output_dir,
            "--aspect", req.aspect,
        ]
        if req.facecam:
            clipper_cmd.extend(["--facecam", req.facecam])
        if req.show_hook:
            clipper_cmd.append("--show-hook")

        code = run_command_streamed(clipper_cmd, "Step 1/2: Rendering Clips (clipper.py)")
        if code != 0:
            with state_lock:
                pipeline_state["status"] = "error"
                pipeline_state["step"] = f"clipper.py failed (exit code {code})"
                pipeline_state["running"] = False
            return

        if req.auto_upload:
            uploader_cmd = [
                sys.executable, "-u", "uploader.py",
                "--json", req.json_path,
                "--clips-dir", output_dir,
            ]
            code = run_command_streamed(uploader_cmd, "Step 2/2: Uploading to YouTube (uploader.py)")
            if code != 0:
                append_log("\nUpload failed — files are kept safe (cleanup skipped).")
                with state_lock:
                    pipeline_state["status"] = "error"
                    pipeline_state["step"] = f"uploader.py failed (exit code {code})"
                    pipeline_state["running"] = False
                return

            # Clean the full folder only after the upload succeeds 100%.
            cleanup_job_files(output_dir, req.json_path, req.video_id)

        with state_lock:
            pipeline_state["status"] = "completed"
            pipeline_state["step"] = (
                "All clips uploaded & folder cleaned!"
                if req.auto_upload
                else "All clips rendered (saved locally)!"
            )
            pipeline_state["running"] = False
        append_log("\n=== PIPELINE COMPLETED SUCCESSFULLY ===")

    except Exception as e:
        append_log(f"\nUnexpected error: {e}")
        with state_lock:
            pipeline_state["status"] = "error"
            pipeline_state["step"] = f"Error: {e}"
            pipeline_state["running"] = False


@app.get("/")
def serve_dashboard():
    return FileResponse("index.html")


@app.post("/api/upload-json")
async def upload_json(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Upload a .json file only.")

    raw_bytes = await file.read()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON format.")

    meta = data.get("video_metadata") or {}
    video_url = meta.get("url")
    clips = data.get("clips") or []
    if not video_url or not clips:
        raise HTTPException(status_code=400, detail="JSON requires 'video_metadata.url' and 'clips'.")

    video_id = re.sub(r"[^a-zA-Z0-9_-]", "", meta.get("video_id") or f"vid_{int(time.time())}")
    saved_json_path = os.path.join(JOBS_DIR, f"{video_id}_{int(time.time())}.json")
    with open(saved_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # Capture a preview at the first clip's start_time so the in-game facecam is visible.
    sample_ts = clips[0].get("start_time", "00:01:00")
    preview_filename = f"{video_id}.jpg"
    preview_path = os.path.join(PREVIEWS_DIR, preview_filename)

    has_preview = os.path.exists(preview_path)
    if not has_preview:
        has_preview = extract_preview_frame(video_url, sample_ts, preview_path)

    return {
        "json_path": saved_json_path,
        "video_id": video_id,
        "title": meta.get("title", "Untitled Video"),
        "channel": meta.get("channel", "Unknown Channel"),
        "clip_count": len(clips),
        "clips": [{"clip_number": c.get("clip_number"), "title": c.get("title"), "start_time": c.get("start_time"), "end_time": c.get("end_time")} for c in clips],
        "preview_url": f"/previews/{preview_filename}?t={int(time.time())}" if has_preview else None,
    }


@app.post("/api/start")
def start_pipeline(req: StartRequest):
    with state_lock:
        if pipeline_state["running"]:
            raise HTTPException(status_code=409, detail="A pipeline is already running. Please wait.")
        pipeline_state["running"] = True
        pipeline_state["status"] = "running"
        pipeline_state["step"] = "Starting pipeline..."
        pipeline_state["logs"] = []

    thread = threading.Thread(target=worker_pipeline, args=(req,), daemon=True)
    thread.start()
    return {"started": True}


@app.get("/api/status")
def get_status():
    with state_lock:
        return dict(pipeline_state)