"""The Social tab's video composer.

One title, one audio track, one picture (or a source video) and a description go in; an
MP4 comes out, rendered by engine/video_maker.py in a child process so a long render
never blocks the app.

Files live under <data>/video:
    inputs/   audio files, pictures and source videos
    output/   the .mp4 files that are ready to post
    work/     spec files and the composed frame (safe to delete)
"""
import importlib.util
import json
import os
import re
import shutil
import sys
import time

from . import config, tasks

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv")
FORMATS = (
    {"key": "16:9", "label": "16:9 - YouTube, Facebook, LinkedIn", "w": 1920, "h": 1080},
    {"key": "9:16", "label": "9:16 - Reels, Shorts, TikTok", "w": 1080, "h": 1920},
    {"key": "1:1", "label": "1:1 - square feed", "w": 1080, "h": 1080},
    {"key": "4:5", "label": "4:5 - Instagram and Facebook feed", "w": 1080, "h": 1350},
)
SIZES = (
    {"key": "1080p", "label": "1080p - full quality", "scale": 1.0},
    {"key": "720p", "label": "720p - smaller file", "scale": 0.6667},
    {"key": "480p", "label": "480p - lightest", "scale": 0.4444},
)
MAX_UPLOAD = 400 * 1024 * 1024
TITLE_LIMIT = 120
DESCRIPTION_LIMIT = 2200
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# ------------------------------------------------------------------ paths
def root_dir():
    return os.path.join(config.DATA_DIR, "video")


def inputs_dir():
    return os.path.join(root_dir(), "inputs")


def output_dir():
    return os.path.join(root_dir(), "output")


def work_dir():
    return os.path.join(root_dir(), "work")


def ensure_dirs():
    for path in (inputs_dir(), output_dir(), work_dir()):
        os.makedirs(path, exist_ok=True)


def clean_name(name):
    """A bare file name, no directories, nothing that Windows rejects."""
    name = _BAD.sub("", os.path.basename(str(name or ""))).strip().strip(".")
    return name[:160]


def _inside(directory, name):
    """Absolute path for name inside directory, or "" (blocks ../ and absolute input)."""
    safe = clean_name(name)
    if not safe:
        return ""
    full = os.path.abspath(os.path.join(directory, safe))
    if os.path.dirname(full) != os.path.abspath(directory):
        return ""
    return full


def file_for(kind, name):
    """kind: 'input' or 'output'."""
    directory = inputs_dir() if kind == "input" else output_dir()
    full = _inside(directory, name)
    return full if full and os.path.isfile(full) else ""


def kind_of(name):
    """'audio', 'image', 'video' or '' - decided by the extension, never by the caller."""
    lower = clean_name(name).lower()
    if lower.endswith(AUDIO_EXT):
        return "audio"
    if lower.endswith(IMAGE_EXT):
        return "image"
    if lower.endswith(VIDEO_EXT):
        return "video"
    return ""


# ------------------------------------------------------------------ geometry
def dims(fmt, size):
    """(width, height) for a format key and a size key, rounded to an even number."""
    base = next((f for f in FORMATS if f["key"] == fmt), FORMATS[0])
    scale = next((s["scale"] for s in SIZES if s["key"] == size), 1.0)
    width = int(round(base["w"] * scale / 2.0)) * 2
    height = int(round(base["h"] * scale / 2.0)) * 2
    return width, height


# ------------------------------------------------------------------ listing
def _list(directory, extensions):
    items = []
    try:
        names = os.listdir(directory)
    except OSError:
        return items
    for name in names:
        if not name.lower().endswith(extensions):
            continue
        full = os.path.join(directory, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        items.append({"name": name, "size": stat.st_size, "mb": round(stat.st_size / 1048576.0, 2),
                      "mtime": int(stat.st_mtime)})
    items.sort(key=lambda x: (-x["mtime"], x["name"].lower()))
    return items


def deps():
    """What the renderer needs: an ffmpeg and Pillow. Nothing else."""
    have_ffmpeg = bool(shutil.which("ffmpeg"))
    have_pillow = importlib.util.find_spec("PIL") is not None
    return {"ffmpeg": have_ffmpeg, "pillow": have_pillow,
            "ready": bool(have_ffmpeg and have_pillow)}


# ------------------------------------------------------------------ files in
def save_upload(name, data):
    ensure_dirs()
    safe = clean_name(name)
    if not safe:
        return {"ok": False, "error": "That file has no usable name."}
    kind = kind_of(safe)
    if not kind:
        return {"ok": False, "error": "Drop an audio file, a picture or a video."}
    if len(data) > MAX_UPLOAD:
        return {"ok": False, "error": "That file is larger than 400 MB."}
    target = _inside(inputs_dir(), safe)
    if not target:
        return {"ok": False, "error": "That file name is not allowed."}
    with open(target, "wb") as fh:
        fh.write(data)
    return {"ok": True, "name": safe, "size": len(data), "kind": kind}


def remove(kind, name):
    full = file_for(kind, name)
    if not full:
        return {"ok": False, "error": "That file is not here."}
    try:
        os.remove(full)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "removed": os.path.basename(full)}


# ------------------------------------------------------------------ spec
def _slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return value[:60]


def build_spec(payload):
    """Turn the page's form into the renderer's spec. Returns (spec, error)."""
    payload = payload or {}
    title = str(payload.get("title") or "").strip()[:TITLE_LIMIT]
    description = str(payload.get("description") or "").strip()[:DESCRIPTION_LIMIT]

    media_name = clean_name(payload.get("media"))
    if not media_name:
        return None, "Add a picture or a video."
    media_kind = kind_of(media_name)
    media = file_for("input", media_name)
    if not media or media_kind not in ("image", "video"):
        return None, "That picture or video is not in the upload folder any more."

    audio_name = clean_name(payload.get("audio"))
    audio = ""
    if audio_name:
        if kind_of(audio_name) != "audio":
            return None, "That file is not an audio file."
        audio = file_for("input", audio_name)
        if not audio:
            return None, "That audio file is not in the upload folder any more."
    if media_kind == "image" and not audio:
        return None, "A picture needs an audio track: the audio sets how long the video is."

    ok = deps()
    if not ok["ready"]:
        missing = [name for name, have in (("ffmpeg", ok["ffmpeg"]), ("Pillow", ok["pillow"]))
                   if not have]
        return None, ("The renderer still needs %s. Install it and try again."
                      % " and ".join(missing))

    fmt = str(payload.get("format") or "16:9")
    if fmt not in [f["key"] for f in FORMATS]:
        fmt = "16:9"
    size = str(payload.get("size") or "1080p")
    if size not in [s["key"] for s in SIZES]:
        size = "1080p"
    width, height = dims(fmt, size)

    slug = _slug(title) or _slug(os.path.splitext(media_name)[0]) or "video"
    output = "%s_%s.mp4" % (slug, time.strftime("%Y%m%d-%H%M%S"))
    spec = {
        "title": title,
        "description": description,
        "audio": audio,
        "media": media,
        "media_kind": media_kind,
        "format": fmt,
        "size": size,
        "width": width,
        "height": height,
        "output": output,
        "out_dir": output_dir(),
        "work_dir": work_dir(),
    }
    return spec, ""


def _command(spec_path):
    """How to call the renderer: the app itself, so it works frozen as well."""
    if config.FROZEN:
        return [sys.executable, "--video-maker", "--spec", spec_path]
    return [sys.executable, os.path.join(config.APP_DIR, "leadstudio.py"),
            "--video-maker", "--spec", spec_path]


def make(payload):
    """Start a render. Returns (task_id, error)."""
    spec, error = build_spec(payload)
    if error:
        return None, error
    ensure_dirs()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    spec_path = os.path.join(work_dir(), "spec_%s.json" % stamp)
    with open(spec_path, "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=1)
    label = spec["title"] or spec["output"]
    task_id = tasks.start("Video: %s" % label, _command(spec_path),
                          cwd=config.APP_DIR if not config.FROZEN else None,
                          env={"LS_ROOT": root_dir()})
    return task_id, ""


# ------------------------------------------------------------------ state
def state():
    ensure_dirs()
    return {
        "audio": _list(inputs_dir(), AUDIO_EXT),
        "images": _list(inputs_dir(), IMAGE_EXT),
        "videos": _list(inputs_dir(), VIDEO_EXT),
        "outputs": _list(output_dir(), VIDEO_EXT),
        "deps": deps(),
        "dirs": {"inputs": inputs_dir(), "output": output_dir()},
        "formats": [dict(f) for f in FORMATS],
        "sizes": [dict(s) for s in SIZES],
        "tasks": tasks.recent(5),
        "running": tasks.running(),
    }
