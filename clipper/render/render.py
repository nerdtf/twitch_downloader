"""Render one vertical clip: accurate trim -> blur/layout -> subtitles + hook -> H.264 (VideoToolbox)."""

import logging
from pathlib import Path

from clipper.judge.transcribe import Word, transcribe_window
from clipper.media.ffmpeg import FFMPEG, probe_video, run
from clipper.render.layout import OUT_H, OUT_W, Layout, filtergraph, hook_y, sub_center_y
from clipper.render.subtitles import BAND_H, build_subtitle_track, render_hook

log = logging.getLogger(__name__)
HOOK_SECONDS = 3.0


def render_clip(
    src: Path,
    start: float,
    duration: float,
    layout: Layout,
    out_dir: Path,
    hook: str = "",
    language: str = "ru",
    words: list[Word] | None = None,
) -> dict:
    """`start` is seconds inside `src`. Returns paths and the words used for subtitles."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    if words is None:  # transcribe the exact cut so subtitles line up with this file
        words = transcribe_window(src, start, duration, language=language).words
    subs = build_subtitle_track(words, duration, work / "subs")
    info = probe_video(src)
    fps = min(round(info["fps"]), 60) or 30

    inputs = ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{duration:.3f}"]
    inputs += ["-f", "concat", "-safe", "0", "-i", str(subs)]
    graph = [filtergraph(layout, info["width"], info["height"], "0:v", "base")]
    graph.append(f"[1:v]format=rgba,fps={fps}[subs]")
    sub_y = sub_center_y(layout, info["width"], info["height"]) - BAND_H // 2
    graph.append(f"[base][subs]overlay=0:{sub_y}:shortest=0:eof_action=pass[withsubs]")
    last = "withsubs"
    if hook.strip():
        hook_png = render_hook(hook, work / "hook.png")
        inputs += ["-loop", "1", "-t", f"{HOOK_SECONDS:.2f}", "-i", str(hook_png)]
        graph.append("[2:v]format=rgba,fade=t=out:st=2.5:d=0.5:alpha=1[hook]")
        y = hook_y(layout, info["width"], info["height"])
        graph.append(f"[withsubs][hook]overlay=0:{y}:eof_action=pass[withhook]")
        last = "withhook"
    graph.append(f"[{last}]fps={fps},format=yuv420p[v]")

    out = out_dir / "clip.mp4"
    enc = [
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "10M",
        "-maxrate",
        "14M",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
    ]
    run(
        [
            *FFMPEG,
            *inputs,
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-t",
            f"{duration:.3f}",
            *enc,
            str(out),
        ]
    )
    log.info("rendered %s (%dx%d, %.1fs)", out, OUT_W, OUT_H, duration)
    return {"clip": out, "words": words}
