import json
import subprocess

from clipper.judge.transcribe import Word
from clipper.media.ffmpeg import probe_duration, probe_video
from clipper.render.caption import build_caption
from clipper.render.layout import Layout, filtergraph, hook_y, load_layout
from clipper.render.render import render_clip
from clipper.render.subtitles import build_subtitle_track, chunk_words


def words(*spec):
    return [Word(t, a, b, 1.0) for t, a, b in spec]


def test_chunking_respects_limits_and_gaps():
    w = words(
        ("раз", 0, 0.3),
        ("два", 0.3, 0.6),
        ("три", 0.6, 0.9),
        ("четыре", 0.9, 1.2),
        ("пять", 3.0, 3.3),
    )
    chunks = chunk_words(w, max_words=3)
    assert [len(c.words) for c in chunks] == [3, 1, 1]  # max words, then the 1.8 s gap splits


def test_subtitle_track_durations_cover_clip(tmp_path):
    listing = build_subtitle_track(words(("привет", 1.0, 1.4), ("мир", 1.5, 2.0)), 5.0, tmp_path)
    durs = [
        float(x.split()[1]) for x in listing.read_text().splitlines() if x.startswith("duration")
    ]
    assert abs(sum(durs) - 5.0) < 0.02


def test_layouts_and_hook_position():
    assert load_layout("buster").facecam == (0, 0, 600, 338)
    assert load_layout("nobody").kind == "fit_blur"
    split = Layout(kind="split", facecam=(0, 0, 600, 338), blur=[(10, 10, 100, 50)])
    g = filtergraph(split, 1920, 1080)
    assert "vstack" in g and "gblur" in g and g.endswith("[vout]")
    assert hook_y(split, 1920, 1080) > 400  # below the face, not on it
    assert "overlay=(W-w)/2:(H-h)/2" in filtergraph(Layout(), 1280, 720)


def test_caption_has_twitch_line():
    assert (
        build_caption(
            "buster",
            "Заголовок",
            {"required_hashtags": ["#x"], "caption_line": "twitch.tv/{login}"},
        )
        == "Заголовок\n#x\ntwitch.tv/buster\n"
    )


def test_render_smoke(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1920x1080:r=30",
            "-f",
            "lavfi",
            "-i",
            "sine=f=440",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(src),
        ],
        check=True,
    )
    out = render_clip(
        src,
        1.0,
        4.0,
        Layout(kind="split", facecam=(0, 0, 600, 338), blur=[(1700, 40, 200, 140)]),
        tmp_path / "out",
        hook="Тест хука",
        words=words(("тест", 0.2, 0.8), ("клипа", 0.9, 1.5)),
    )
    info = probe_video(out["clip"])
    assert (info["width"], info["height"]) == (1080, 1920)
    assert abs(probe_duration(out["clip"]) - 4.0) < 0.2
    json.dumps({"ok": True})
