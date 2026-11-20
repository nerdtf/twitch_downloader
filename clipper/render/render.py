"""Render one vertical clip: accurate trim -> blur/layout -> subtitles + hook -> H.264 (VideoToolbox)."""

import logging
from pathlib import Path

from clipper.judge.transcribe import Word, transcribe_window
from clipper.media.ffmpeg import FFMPEG, probe_video, run
from clipper.render.layout import OUT_H, OUT_W, Layout, filtergraph, hook_y, sub_center_y
from clipper.render.style import STYLES, YELLOW, Style
from clipper.render.subtitles import BAND_H, build_subtitle_track, render_hook, render_tag

log = logging.getLogger(__name__)
HOOK_SECONDS = 3.0
PREROLL = 8.0  # seconds decoded before the cut so the first frame is exact


def render_clip(
    src: Path,
    start: float,
    duration: float,
    layout: Layout,
    out_dir: Path,
    hook: str = "",
    language: str = "ru",
    words: list[Word] | None = None,
    style: Style | None = None,
    event_at: float | None = None,
    login: str = "",
    out_name: str = "clip.mp4",
) -> dict:
    """`start` is seconds inside `src`. Returns paths and the words used for subtitles.
    `style` adds looks (see clipper.render.style); `event_at` = the moment, seconds into the clip."""
    st = style or STYLES["base"]
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".work"
    if words is None:  # transcribe the exact cut so subtitles line up with this file
        words = transcribe_window(src, start, duration, language=language).words
    subs = build_subtitle_track(words, duration, work / f"subs_{st.name}", style=st.sub)
    info = probe_video(src)
    fps = min(round(info["fps"]), 60) or 30
    n_in = 2  # 0 = video, 1 = subtitles

    # Seeking a .ts lands after the next keyframe (Twitch: every ~4 s), so clips started with up
    # to 6 s of missing video (2026-11-20). Seek PREROLL earlier and trim exactly in the graph.
    pre = min(start, PREROLL)
    inputs = ["-ss", f"{start - pre:.3f}", "-i", str(src), "-t", f"{duration + pre:.3f}"]
    inputs += ["-f", "concat", "-safe", "0", "-i", str(subs)]
    graph = [
        f"[0:v]trim=start={pre:.3f},setpts=PTS-STARTPTS[vin]",
        f"[0:a]atrim=start={pre:.3f},asetpts=PTS-STARTPTS[a]",
        filtergraph(layout, info["width"], info["height"], "vin", "raw"),
    ]
    fx = [f for f in st.grade.split(",") if f]
    if st.punch > 0:
        at = event_at if event_at is not None else duration / 2
        z = f"1+{st.punch}*exp(-pow((it-{at:.2f})/0.35,2))"
        fx.append(
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={OUT_W}x{OUT_H}:fps={fps}"
        )
    if st.flash:
        fx.append("fade=t=in:st=0:d=0.3:color=white")
    graph.append(f"[raw]{','.join(fx) or 'null'}[base]")
    graph.append(f"[1:v]format=rgba,fps={fps}[subs]")
    sub_y = sub_center_y(layout, info["width"], info["height"]) - BAND_H // 2
    graph.append(f"[base][subs]overlay=0:{sub_y}:shortest=0:eof_action=pass[withsubs]")
    last = "withsubs"
    if hook.strip() and st.hook != "none":
        if st.hook == "sticker":
            hook_png = render_hook(hook, work / f"hook_{st.name}.png", size=80, fill=YELLOW,
                                   angle=3)  # fmt: skip
            fade = "fade=t=in:st=0:d=0.15:alpha=1,fade=t=out:st=2.6:d=0.4:alpha=1"
        else:
            hook_png = render_hook(hook, work / "hook.png")
            fade = "fade=t=out:st=2.5:d=0.5:alpha=1"
        inputs += ["-loop", "1", "-t", f"{HOOK_SECONDS:.2f}", "-i", str(hook_png)]
        graph.append(f"[{n_in}:v]format=rgba,{fade}[hook]")
        n_in += 1
        y = hook_y(layout, info["width"], info["height"]) + (40 if st.hook == "sticker" else 0)
        graph.append(f"[{last}][hook]overlay=0:{y}:eof_action=pass[withhook]")
        last = "withhook"
    if st.tag and login:
        tag_png = render_tag(f"twitch.tv/{login}", work / "tag.png")
        inputs += ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(tag_png)]
        graph.append(f"[{n_in}:v]format=rgba[tag]")
        n_in += 1
        graph.append(f"[{last}][tag]overlay=W-w-28:28:eof_action=pass[withtag]")  # top-right
        last = "withtag"
    if st.progress:
        graph.append(f"color=c={st.progress}:s={OUT_W}x12:r={fps}[pbar]")
        graph.append(f"[{last}][pbar]overlay=x='-w+w*t/{duration:.3f}':y=H-12:shortest=1[withbar]")
        last = "withbar"
    graph.append(f"[{last}]fps={fps},format=yuv420p[v]")

    out = out_dir / out_name
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
            "[a]",
            "-t",
            f"{duration:.3f}",
            *enc,
            str(out),
        ]
    )
    log.info("rendered %s (%dx%d, %.1fs)", out, OUT_W, OUT_H, duration)
    return {"clip": out, "words": words}
