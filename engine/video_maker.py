"""Render one social video from a spec file.

Called as a child process by engine/video.py (the app itself runs with --video-maker),
so a slow encode never blocks the server and Cancel can kill ffmpeg.

The spec is the JSON engine/video.build_spec() wrote:
    title, description          the caption drawn into the cover frame
    media, media_kind           one picture or one source video
    audio                       an audio track; required for a picture
    format, size, width, height the output geometry
    output                      the file name inside out_dir

A picture becomes a still cover frame (picture + gradient + title + description) held
for the length of the audio. A source video is scaled and cropped to the format, and the
audio track replaces (or supplies) its sound.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

if __package__ in (None, ""):                       # run as a bare script
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from engine import config, video                # noqa: E402
else:
    from . import config, video

FONT_DIRS = [os.path.join(config.APP_DIR, "fonts"),
             os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts"]
FONTS = {
    "bold": ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"],
}
_FONTS = {}


def say(*args):
    print(*args, flush=True)


# ------------------------------------------------------------------ tools
def ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit("ffmpeg is not installed and imageio-ffmpeg is not available either.")
    return imageio_ffmpeg.get_ffmpeg_exe()


def flags():
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def duration(path):
    """Length of a media file in seconds, read from ffmpeg's own banner."""
    result = subprocess.run([ffmpeg(), "-hide_banner", "-i", path], capture_output=True,
                            text=True, errors="replace", creationflags=flags())
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
    if not match:
        raise RuntimeError("Could not read the length of " + os.path.basename(path))
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def run_stream(cmd):
    """Run ffmpeg and echo the interesting lines, so the page shows live progress."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
                            errors="replace", creationflags=flags())
    keep = ("frame=", "time=", "Error", "error", "Invalid", "No such", "Output #", "Video:")
    try:
        for chunk in proc.stdout:
            for line in chunk.replace("\r", "\n").splitlines():
                line = line.strip()
                if line and any(word in line for word in keep):
                    say(line)
        code = proc.wait()
    finally:
        try:
            proc.stdout.close()
        except (OSError, ValueError):
            pass
    if code:
        raise RuntimeError("ffmpeg stopped with code %d." % code)


# ------------------------------------------------------------------ frame
def font(size, bold=False):
    from PIL import ImageFont
    kind = "bold" if bold else "regular"
    key = (kind, size)
    if key in _FONTS:
        return _FONTS[key]
    loaded = None
    for directory in FONT_DIRS:
        for name in FONTS[kind]:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                loaded = ImageFont.truetype(path, size)
                break
        if loaded:
            break
    if loaded is None:
        loaded = ImageFont.load_default()
    _FONTS[key] = loaded
    return loaded


def cover(img, width, height):
    """Scale and centre-crop, never stretch."""
    from PIL import Image
    ratio = max(width / float(img.width), height / float(img.height))
    resized = img.resize((max(1, int(round(img.width * ratio))),
                          max(1, int(round(img.height * ratio)))), Image.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def shade(width, height, start=0.45, peak=205):
    """A transparent-to-dark ramp over the lower part, so white text always reads."""
    from PIL import Image
    column = Image.new("L", (1, height))
    rows = []
    for y in range(height):
        edge = start * height
        if y < edge:
            rows.append(0)
        else:
            rows.append(int(peak * min(1.0, (y - edge) / max(1.0, height - edge))))
    column.putdata(rows)
    return column.resize((width, height))


def wrap(draw, text, face, max_width, max_lines):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if draw.textlength(candidate, font=face) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    last = lines[-1]
    while last and draw.textlength(last + " ...", font=face) > max_width:
        last = last.rsplit(" ", 1)[0] if " " in last else last[:-1]
    lines[-1] = (last + " ...").strip()
    return lines


def fit(draw, text, max_width, max_lines, size, bold, floor):
    """The largest font size at which text fits in max_lines."""
    while size > floor:
        face = font(size, bold)
        if len(wrap(draw, text, face, max_width, max_lines + 1)) <= max_lines:
            return face
        size -= 2
    return font(max(floor, size), bold)


def compose(spec):
    """Build the still frame for a picture-backed video. Returns the PNG path."""
    from PIL import Image, ImageDraw
    width, height = int(spec["width"]), int(spec["height"])
    image = Image.open(spec["media"]).convert("RGB")
    frame = cover(image, width, height).convert("RGBA")
    veil = Image.new("RGBA", (width, height), (6, 10, 18, 255))
    veil.putalpha(shade(width, height))
    frame.alpha_composite(veil)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    margin = int(width * 0.09)
    max_width = width - margin * 2
    title = str(spec.get("title") or "").strip()
    description = str(spec.get("description") or "").strip()
    base = max(26, min(int(min(width, height) * 0.075), 108))
    title_face = fit(draw, title, max_width, 3, base, True, max(20, base // 2)) if title else None
    desc_face = fit(draw, description, max_width, 4, max(18, int(base * 0.52)), False,
                    max(14, base // 4)) if description else None
    title_lines = wrap(draw, title, title_face, max_width, 3) if title_face else []
    desc_lines = wrap(draw, description, desc_face, max_width, 4) if desc_face else []

    title_step = int(title_face.size * 1.34) if title_face else 0
    desc_step = int(desc_face.size * 1.45) if desc_face else 0
    gap = int(desc_face.size * 1.6) if (title_lines and desc_lines) else 0
    total = len(title_lines) * title_step + gap + len(desc_lines) * desc_step
    y = height - int(height * 0.11) - total

    for line in title_lines:
        draw.text((margin, y), line, font=title_face, fill=(255, 255, 255, 255))
        y += title_step
    if title_lines and desc_lines and gap:
        y += int(gap * 0.4)
        draw.line((margin, y, margin + int(width * 0.09), y), fill=(255, 255, 255, 190), width=4)
        y += int(gap * 0.6)
    for line in desc_lines:
        draw.text((margin, y), line, font=desc_face, fill=(226, 233, 245, 235))
        y += desc_step

    frame.alpha_composite(layer)
    os.makedirs(spec["work_dir"], exist_ok=True)
    target = os.path.join(spec["work_dir"], "frame.png")
    frame.convert("RGB").save(target, "PNG")
    say("Cover frame: %dx%d, %d title line(s), %d description line(s)"
        % (width, height, len(title_lines), len(desc_lines)))
    return target


# ------------------------------------------------------------------ render
def render(spec):
    width, height = int(spec["width"]), int(spec["height"])
    output = os.path.join(spec["out_dir"], video.clean_name(spec["output"]))
    os.makedirs(spec["out_dir"], exist_ok=True)
    fmt = "%dx%d" % (width, height)
    scale = "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=30" % (
        width, height, width, height)

    if spec["media_kind"] == "image":
        frame = compose(spec)
        seconds = duration(spec["audio"])
        say("Rendering %s - a still cover held for %s" % (fmt, as_clock(seconds)))
        run_stream([ffmpeg(), "-hide_banner", "-y", "-loop", "1", "-framerate", "30",
                    "-i", frame, "-i", spec["audio"], "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", output])
    else:
        command = [ffmpeg(), "-hide_banner", "-y", "-i", spec["media"]]
        if spec.get("audio"):
            command += ["-i", spec["audio"], "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        else:
            command += ["-map", "0:v:0", "-map", "0:a?"]
        command += ["-vf", scale, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", output]
        say("Rendering %s from %s" % (fmt, os.path.basename(spec["media"])))
        run_stream(command)

    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise RuntimeError("ffmpeg reported success but wrote no file.")
    mb = os.path.getsize(output) / 1048576.0
    say("Saved %s (%.2f MB, %s, %s)" % (os.path.basename(output), mb, fmt, spec["format"]))


def as_clock(seconds):
    minutes, rest = divmod(int(seconds), 60)
    return "%d:%02d" % (minutes, rest)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="video_maker")
    parser.add_argument("--spec", required=True)
    args = parser.parse_args(argv)
    with open(args.spec, "r", encoding="utf-8") as handle:
        spec = json.load(handle)
    try:
        render(spec)
    except (RuntimeError, OSError, KeyError, ValueError) as exc:
        say("Render failed: %s" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
