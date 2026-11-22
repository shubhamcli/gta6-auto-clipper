import argparse
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys

# ================= DEFAULT CONFIG =================
JSON_CANDIDATES = [
    "techno_gamerz_gta6_ep1_clips.json",
    "techno_gamerz_gta6_ep1_clips_3.json",
    "techno_gamerz_gta6_ep1_clips_2.json",
]
DEFAULT_OUTPUT_DIR = "final_clips_pro"
DEFAULT_VIDEO_ASPECT = "1:1"  # Fallback layout when no facecam is selected
WORDS_PER_CHUNK = 3           # Maximum spoken words shown on screen at once (2 or 3 works best)
FONT_NAME = "Arial"           # Impact can also be tried for gaming shorts
KEEP_RAW_LANDSCAPE = True     # Keep true to avoid re-downloading during manual runs (the server cleans it after upload)
DEFAULT_SHOW_HOOK = False     # Default False: keep the video clean
# ==================================================


# --- Pure-Python Devanagari to Clean Romanized Hindi Converter ---
VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o", "ऍ": "e",
}
CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}
NUKTA_MAP = {
    "क": "q", "ख": "kh", "ग": "gh", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f",
}
MATRAS = {
    "ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e",
}
HALANT = "्"
NUKTA = "़"
NASALS = {"ं", "ँ"}


def devanagari_word_to_hinglish(word: str) -> str:
    """Convert a single Hindi word into readable Romanized Hindi."""
    out = []
    n = len(word)
    i = 0
    while i < n:
        ch = word[i]
        if ch in VOWELS:
            out.append(VOWELS[ch])
            i += 1
        elif ch in CONSONANTS:
            base = CONSONANTS[ch]
            if i + 1 < n and word[i + 1] == NUKTA:
                base = NUKTA_MAP.get(ch, base)
                i += 1

            nxt = word[i + 1] if i + 1 < n else ""
            if nxt == HALANT:
                out.append(base)
                i += 2
            elif nxt in MATRAS:
                out.append(base + MATRAS[nxt])
                i += 2
            elif nxt in NASALS:
                out.append(base + "an")
                i += 2
            else:
                if i + 1 < n and (word[i + 1] in CONSONANTS or word[i + 1] in VOWELS):
                    out.append(base + "a")
                else:
                    out.append(base)
                i += 1
        elif ch in NASALS:
            out.append("n")
            i += 1
        elif ch in ("।", "॥"):
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def to_hinglish(text: str) -> str:
    """Convert only Devanagari words in mixed Hindi and English text to Romanized Hindi; preserve English text."""
    tokens = text.split()
    converted = []
    for tok in tokens:
        if any("\u0900" <= c <= "\u097F" for c in tok):
            converted.append(devanagari_word_to_hinglish(tok))
        else:
            converted.append(tok)
    return " ".join(converted)


def check_dependencies():
    """Verify that yt-dlp and ffmpeg are available on the system PATH."""
    missing = [tool for tool in ("yt-dlp", "ffmpeg") if shutil.which(tool) is None]
    if missing:
        print(f"Error: These tools were not found on the system PATH: {', '.join(missing)}")
        print("Install command: winget install yt-dlp.yt-dlp Gyan.FFmpeg")
        sys.exit(1)


def find_json_file(explicit_path: str | None = None) -> str:
    """Find the clips JSON file from the CLI argument or the project folder."""
    if explicit_path:
        if os.path.exists(explicit_path):
            return explicit_path
        print(f"Error: The specified JSON file was not found: {explicit_path}")
        sys.exit(1)

    for path in JSON_CANDIDATES:
        if os.path.exists(path):
            return path
    print(f"Error: No JSON file was found ({', '.join(JSON_CANDIDATES)}).")
    sys.exit(1)


def parse_facecam_arg(facecam_str: str | None):
    """Parse and validate the normalized facecam box 'fx,fy,fw,fh' (0.0 to 1.0)."""
    if not facecam_str:
        return None
    try:
        fx, fy, fw, fh = [float(v.strip()) for v in facecam_str.split(",")]
        fx = max(0.0, min(0.95, fx))
        fy = max(0.0, min(0.95, fy))
        fw = max(0.05, min(1.0 - fx, fw))
        fh = max(0.05, min(1.0 - fy, fh))
        return (fx, fy, fw, fh)
    except Exception:
        print(f"Warning: Invalid facecam coordinates '{facecam_str}', falling back to 1:1 mode.")
        return None


def time_to_sec(ts: str) -> float:
    """Convert an HH:MM:SS(.mmm) or MM:SS(.mmm) timestamp into total seconds."""
    ts = ts.strip().replace(",", ".")
    parts = [float(p) for p in ts.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def sec_to_ass(seconds: float) -> str:
    """Convert seconds into ASS subtitle format (H:MM:SS.cs)."""
    seconds = max(0.0, seconds)
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        secs += 1
        cs = 0
    if secs >= 60:
        mins += 1
        secs -= 60
    if mins >= 60:
        hrs += 1
        mins -= 60
    return f"{hrs}:{mins:02}:{secs:02}.{cs:02}"


def clean_ass_text(text: str, convert_hinglish: bool = True) -> str:
    """
    - Convert Hindi/Devanagari text to Romanized Hindi.
    - Preserve English text with its natural casing.
    - Escape ASS special characters ({, }, \\).
    """
    text = html.unescape(text)
    if convert_hinglish:
        text = to_hinglish(text)
    ascii_only = re.sub(r"[^\x20-\x7E]", "", text)
    safe = ascii_only.replace("\\", "").replace("{", "(").replace("}", ")")
    return re.sub(r"\s+", " ", safe).strip()


def get_layout_settings(aspect: str, has_facecam: bool) -> dict:
    """
    Determine layout and caption placement:
    - Split-screen with facecam: 260px top area, 4:3 gameplay (1080x810 at y=260),
      facecam (1080x500 at y=1070), and 350px bottom safe zone.
      Captions appear in the center seam (x=540, y=1070) as small, crisp text.
    - No facecam (1:1): centered 1080x1080 gameplay and lower-center captions (y=1340).
    """
    if has_facecam:
        return {
            "mode": "split_facecam",
            "hook_margin_v": 115,
            "hook_font_size": 46,
            "caption_pos": (540, 1070),  # Centered between gameplay and facecam
            "caption_font_size": 52,     # Small, crisp, and punchy
        }

    if aspect == "3:4":
        return {
            "mode": "single",
            "crop_scale": "crop=ih*3/4:ih,scale=1080:1440",
            "hook_margin_v": 90,
            "hook_font_size": 46,
            "caption_pos": (540, 1280),
            "caption_font_size": 52,
        }
    if aspect == "4:3":
        return {
            "mode": "single",
            "crop_scale": "crop=ih*4/3:ih,scale=1080:810",
            "hook_margin_v": 250,
            "hook_font_size": 50,
            "caption_pos": (540, 1280),
            "caption_font_size": 52,
        }
    # Default "1:1"
    return {
        "mode": "single",
        "crop_scale": "crop=ih:ih,scale=1080:1080",
        "hook_margin_v": 170,
        "hook_font_size": 48,
        "caption_pos": (540, 1340),  # Lower-center position within the Shorts safe area
        "caption_font_size": 52,
    }


def detect_video_language(video_url: str, meta: dict) -> str:
    """
    Auto-detect the YouTube video's original language:
    - For English videos, request only 'en' or 'en-orig' to avoid Hindi translation
    - For Hindi videos, request only 'hi' or 'hi-orig'
    """
    explicit_lang = (meta.get("language") or "").strip()
    if explicit_lang:
        print(f"Using language from JSON metadata: {explicit_lang}")
        return explicit_lang

    print("Auto-detecting original video language from YouTube...")
    try:
        cmd = [
            "yt-dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            video_url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=45)
        info = json.loads(res.stdout)

        auto_caps = info.get("automatic_captions") or {}
        manual_subs = info.get("subtitles") or {}

        # 1. The most accurate option: YouTube adds '-orig' to the original audio track (for example, 'en-orig' or 'hi-orig')
        for key in auto_caps.keys():
            if key.endswith("-orig"):
                print(f"Detected original caption track: {key}")
                return key

        # 2. The video metadata 'language' field (for example, 'en', 'en-US', or 'hi')
        vid_lang = (info.get("language") or "").strip()
        if vid_lang:
            base_lang = vid_lang.split("-")[0].lower()
            if vid_lang in manual_subs or vid_lang in auto_caps:
                print(f"Detected video language: {vid_lang}")
                return vid_lang
            if base_lang in manual_subs or base_lang in auto_caps:
                print(f"Detected video language: {base_lang}")
                return base_lang

        # 3. Prefer a supported manual subtitle language
        for pref in ("en", "hi"):
            if pref in manual_subs:
                print(f"Detected manual subtitle language: {pref}")
                return pref

    except Exception as e:
        print(f"Language auto-detect warning: {e}")

    print("Fallback language: en")
    return "en"


def download_youtube_transcript(video_url: str, video_id: str, output_dir: str, meta: dict) -> tuple[str, str]:
    """
    Download the .vtt transcript only for the video's detected original language.
    Returns: (vtt_path, detected_lang)
    """
    prefix = os.path.join(output_dir, f"yt_subs_{video_id}")
    existing = sorted(glob.glob(f"{prefix}.*.vtt"))
    if existing:
        cached_path = existing[0]
        # Extract the language from the filename: yt_subs_<id>.<lang>.vtt
        parts = os.path.basename(cached_path).split(".")
        cached_lang = parts[-2] if len(parts) >= 3 else "en"
        print(f"Using cached YouTube transcript: {cached_path} (lang: {cached_lang})")
        return cached_path, cached_lang

    detected_lang = detect_video_language(video_url, meta)
    base_lang = detected_lang.split("-")[0].lower()

    # Try the exact detected language first, then its base language if '-orig' fails
    langs_to_try = [detected_lang]
    if base_lang != detected_lang:
        langs_to_try.append(base_lang)

    print(f"Fetching real YouTube transcript for language '{detected_lang}'...")
    for lang in langs_to_try:
        cmd = [
            "yt-dlp",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-lang", lang,
            "--sub-format", "vtt",
            "-o", prefix,
            video_url,
        ]
        subprocess.run(cmd, check=False)

        candidate = f"{prefix}.{lang}.vtt"
        if os.path.exists(candidate):
            print(f"Transcript ready: {candidate}")
            return candidate, lang

        matches = sorted(glob.glob(f"{prefix}.*.vtt"))
        if matches:
            print(f"Transcript ready: {matches[0]}")
            return matches[0], lang

    print(f"Warning: No YouTube transcript was found for '{detected_lang}'. Captions will be skipped.")
    return "", detected_lang


def parse_vtt_words(vtt_path: str, is_english: bool) -> list:
    """Extract exact word-level timestamps from the YouTube VTT file."""
    if not vtt_path or not os.path.exists(vtt_path):
        return []

    convert_hinglish = not is_english

    with open(vtt_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = re.split(r"\r?\n\r?\n", content)
    inline_ts_pattern = re.compile(r"<(\d{2}:\d{2}:\d{2}\.\d{3})>")
    cue_time_pattern = re.compile(
        r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
    )

    has_inline_timestamps = bool(inline_ts_pattern.search(content))
    words_data = []
    last_plain_line = ""

    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        time_match = None
        text_start_idx = 0
        for idx, ln in enumerate(lines):
            m = cue_time_pattern.search(ln)
            if m:
                time_match = m
                text_start_idx = idx + 1
                break

        if not time_match:
            continue

        cue_start = time_to_sec(time_match.group(1))
        cue_end = time_to_sec(time_match.group(2))
        text_lines = lines[text_start_idx:]
        if not text_lines:
            continue

        if has_inline_timestamps:
            tagged_lines = [ln for ln in text_lines if inline_ts_pattern.search(ln)]
            if not tagged_lines:
                continue

            for tline in tagged_lines:
                parts = inline_ts_pattern.split(tline)
                entries = []
                first_txt = re.sub(r"<[^>]+>", "", parts[0]).strip()
                if first_txt:
                    entries.append((cue_start, first_txt))

                for j in range(1, len(parts), 2):
                    ts_val = time_to_sec(parts[j])
                    seg_txt = re.sub(r"<[^>]+>", "", parts[j + 1]).strip() if j + 1 < len(parts) else ""
                    if seg_txt:
                        entries.append((ts_val, seg_txt))

                for k, (w_start, raw_txt) in enumerate(entries):
                    w_end = entries[k + 1][0] if k + 1 < len(entries) else cue_end
                    w_end = max(w_end, w_start + 0.12)
                    for token in raw_txt.split():
                        cleaned = clean_ass_text(token, convert_hinglish=convert_hinglish)
                        if cleaned:
                            words_data.append({
                                "start": w_start,
                                "end": w_end,
                                "word": cleaned,
                            })
        else:
            plain = " ".join(re.sub(r"<[^>]+>", "", ln) for ln in text_lines).strip()
            plain = re.sub(r"\s+", " ", plain)
            if not plain or plain == last_plain_line:
                continue
            last_plain_line = plain

            tokens = [clean_ass_text(t, convert_hinglish=convert_hinglish) for t in plain.split()]
            tokens = [t for t in tokens if t]
            if not tokens:
                continue

            dur = max(0.3, cue_end - cue_start)
            step = dur / len(tokens)
            for idx, tok in enumerate(tokens):
                words_data.append({
                    "start": cue_start + idx * step,
                    "end": cue_start + (idx + 1) * step,
                    "word": tok,
                })

    return words_data


def slice_clip_chunks(all_words: list, clip_start: float, clip_end: float, chunk_size: int) -> list:
    """Extract spoken words within [clip_start, clip_end] and group them into 2-to-3-word chunks."""
    clip_dur = max(1.0, clip_end - clip_start)
    clip_words = []

    for w in all_words:
        if w["end"] <= clip_start or w["start"] >= clip_end:
            continue
        rel_start = max(0.0, w["start"] - clip_start)
        rel_end = min(clip_dur, w["end"] - clip_start)
        if rel_end <= rel_start:
            rel_end = min(clip_dur, rel_start + 0.15)
        clip_words.append({
            "start": rel_start,
            "end": rel_end,
            "word": w["word"],
        })

    chunks = []
    for i in range(0, len(clip_words), chunk_size):
        group = clip_words[i : i + chunk_size]
        text = " ".join(g["word"] for g in group).strip()
        c_start = group[0]["start"]
        c_end = max(group[-1]["end"], c_start + 0.35)
        c_end = min(clip_dur, c_end)
        chunks.append({
            "start": c_start,
            "end": c_end,
            "text": text,
        })

    for i in range(len(chunks) - 1):
        if chunks[i]["end"] > chunks[i + 1]["start"]:
            chunks[i]["end"] = max(chunks[i]["start"] + 0.1, chunks[i + 1]["start"])

    return chunks


def create_ass_file(clip: dict, chunks: list, ass_path: str, layout: dict, show_hook: bool):
    """Create small, crisp, centered animated captions with natural casing and no uppercase filter."""
    clip_start = time_to_sec(clip["start_time"])
    clip_end = time_to_sec(clip["end_time"])
    total_dur = max(1.0, clip_end - clip_start)

    raw_hook = clip.get("hook_idea") or clip.get("title", "")
    hook_text = clean_ass_text(raw_hook, convert_hinglish=True)
    cap_x, cap_y = layout["caption_pos"]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{FONT_NAME},{layout['hook_font_size']},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,8,70,70,{layout['hook_margin_v']},1
Style: Caption,{FONT_NAME},{layout['caption_font_size']},&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    if show_hook and hook_text:
        events.append(
            f"Dialogue: 0,0:00:00.00,{sec_to_ass(total_dur)},Hook,,0,0,0,,{hook_text}"
        )

    # Smooth pop-in bounce animation centered exactly at (cap_x, cap_y)
    pop_anim = (
        rf"{{\an5\pos({cap_x},{cap_y})\fscx82\fscy82"
        rf"\t(0,65,\fscx106\fscy106)\t(65,130,\fscx100\fscy100)}}"
    )
    for ch in chunks:
        events.append(
            f"Dialogue: 0,{sec_to_ass(ch['start'])},{sec_to_ass(ch['end'])},Caption,,0,0,0,,{pop_anim}{ch['text']}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")


def escape_ffmpeg_path(path: str) -> str:
    """Make Windows paths safe for the FFmpeg ASS filter."""
    return path.replace("\\", "/").replace(":", "\\:")


def parse_args():
    parser = argparse.ArgumentParser(description="Auto Shorts Clipper Pro")
    parser.add_argument("--json", dest="json_path", default=None, help="Path to clips JSON file")
    parser.add_argument("--output-dir", dest="output_dir", default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--aspect", dest="aspect", default=DEFAULT_VIDEO_ASPECT, choices=["1:1", "4:3", "3:4"])
    parser.add_argument(
        "--facecam",
        dest="facecam",
        default=None,
        help="Normalized facecam box as fx,fy,fw,fh (0..1). Omit for single-screen mode.",
    )
    parser.add_argument(
        "--show-hook",
        dest="show_hook",
        action="store_true",
        default=DEFAULT_SHOW_HOOK,
        help="Enable top hook text overlay (default: off)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    check_dependencies()

    json_path = find_json_file(args.json_path)
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    facecam_box = parse_facecam_arg(args.facecam)
    layout = get_layout_settings(args.aspect, has_facecam=bool(facecam_box))

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    meta = data["video_metadata"]
    video_url = meta["url"]
    video_id = meta.get("video_id", "video")
    clips = data.get("clips", [])

    mode_label = (
        f"Split-Screen (4:3 Gameplay + Facecam {facecam_box})"
        if facecam_box
        else f"Single-Screen ({args.aspect} in 9:16)"
    )
    print(f"Using JSON: {json_path}")
    print(f"Output Dir: {output_dir}")
    print(f"Mode: {mode_label} | Hook: {'ON' if args.show_hook else 'OFF'} | Captions: Centered ({WORDS_PER_CHUNK} words/chunk)\n")

    vtt_path, detected_lang = download_youtube_transcript(video_url, video_id, output_dir, meta)
    is_english = detected_lang.lower().startswith("en")
    all_words = parse_vtt_words(vtt_path, is_english=is_english)
    caption_mode = "English (As-Is)" if is_english else f"{detected_lang} -> Hinglish"
    print(f"Loaded {len(all_words)} timed spoken words ({caption_mode}).\n")

    success_count = 0

    for clip in clips:
        num = clip["clip_number"]
        start = clip["start_time"]
        end = clip["end_time"]
        safe_title = re.sub(r"[^a-zA-Z0-9_-]", "_", clip["title"]).strip("_")[:45]

        raw_path = os.path.join(output_dir, f"clip_{num}_raw.mp4")
        ass_path = os.path.join(output_dir, f"clip_{num}.ass")
        final_path = os.path.join(output_dir, f"clip_{num}_{safe_title}_PRO.mp4")

        print(f"--- [Clip {num}/{len(clips)}] {clip['title']} ({start} -> {end}) ---")

        try:
            if not os.path.exists(raw_path):
                yt_cmd = [
                    "yt-dlp",
                    "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
                    "--download-sections", f"*{start}-{end}",
                    "--force-keyframes-at-cuts",
                    "--merge-output-format", "mp4",
                    "-o", raw_path,
                    video_url,
                ]
                subprocess.run(yt_cmd, check=True)
            else:
                print(f"Skipping download (already exists): {raw_path}")

            c_start = time_to_sec(start)
            c_end = time_to_sec(end)
            chunks = slice_clip_chunks(all_words, c_start, c_end, WORDS_PER_CHUNK)
            print(f"Synced {len(chunks)} animated caption chunks from YouTube transcript.")

            create_ass_file(clip, chunks, ass_path, layout, show_hook=args.show_hook)
            ass_ffmpeg = escape_ffmpeg_path(ass_path)

            if facecam_box:
                fx, fy, fw, fh = facecam_box
                # Top 260px black -> 4:3 Gameplay (1080x810 at y=260) -> Facecam (1080x500 at y=1070) -> Bottom 350px safe zone
                filter_complex = (
                    f"[0:v]split=2[game_in][cam_in];"
                    f"[game_in]crop=ih*4/3:ih,scale=1080:810,pad=1080:1920:0:260:black[canvas];"
                    f"[cam_in]crop=iw*{fw:.6f}:ih*{fh:.6f}:iw*{fx:.6f}:ih*{fy:.6f},"
                    f"scale=1080:500:force_original_aspect_ratio=increase,crop=1080:500[cam];"
                    f"[canvas][cam]overlay=0:1070,ass='{ass_ffmpeg}'[vout]"
                )
                print("Rendering Split-Screen (Gameplay + Facecam) + Centered Captions...")
                ff_cmd = [
                    "ffmpeg", "-y",
                    "-i", raw_path,
                    "-filter_complex", filter_complex,
                    "-map", "[vout]",
                    "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    final_path,
                ]
            else:
                vf_filter = (
                    f"{layout['crop_scale']},"
                    f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
                    f"ass='{ass_ffmpeg}'"
                )
                print(f"Rendering {args.aspect}-in-9:16 + Centered Captions...")
                ff_cmd = [
                    "ffmpeg", "-y",
                    "-i", raw_path,
                    "-vf", vf_filter,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    final_path,
                ]

            subprocess.run(ff_cmd, check=True)

            if not KEEP_RAW_LANDSCAPE and os.path.exists(raw_path):
                os.remove(raw_path)

            success_count += 1
            print(f"Done: {final_path}\n")

        except subprocess.CalledProcessError as e:
            print(f"Error processing Clip {num}: Command failed with code {e.returncode}\n")
        except Exception as e:
            print(f"Unexpected error on Clip {num}: {e}\n")

    print(f"Finished! {success_count}/{len(clips)} Pro Shorts are ready in the '{output_dir}' folder.")


if __name__ == "__main__":
    main()