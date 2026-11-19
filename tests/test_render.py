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
    assert load_layout("buster").facecam == (0, 0, 490, 274)
    assert load_layout("nobody").kind == "auto"
    split = Layout(kind="split", facecam=(0, 0, 600, 338), blur=[(10, 10, 100, 50)])
    g = filtergraph(split, 1920, 1080)
    assert "vstack" in g and "gblur" in g and g.endswith("[vout]")
    assert hook_y(split, 1920, 1080) > 400  # below the face, not on it
    assert "overlay=(W-w)/2:(H-h)/2" in filtergraph(Layout(), 1280, 720)


def test_split_crop_slides_off_the_webcam():
    import re

    for cam, ok in [
        ((0, 600, 500, 300), lambda x, w: x >= 500),
        ((1450, 0, 470, 300), lambda x, w: x + w <= 1450),
    ]:
        g = filtergraph(Layout(kind="split", facecam=cam), 1920, 1080)
        w, h, x, y = map(int, re.search(r"\[sb\]crop=(\d+):(\d+):(\d+):(\d+)", g).groups())
        assert ok(x, w), (cam, x, w)


def test_resolve_layout_irl_and_no_cam(tmp_path):
    from clipper.render.facecam import resolve_layout

    lay = Layout(kind="auto", facecam=(0, 0, 400, 300))
    assert resolve_layout(lay, tmp_path / "x.ts", 0, 10, "Just Chatting")[0].kind == "fit_blur"
    assert resolve_layout(Layout(kind="auto"), tmp_path / "x.ts", 0, 10)[0].kind == "fit_blur"
    assert (
        resolve_layout(Layout(kind="split", facecam=(0, 0, 4, 4)), tmp_path / "x.ts", 0, 10)[0].kind
        == "split"
    )


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


def test_facecam_list_gives_alternative_boxes(tmp_path):
    from clipper.render.layout import load_layout

    p = tmp_path / "l.yaml"
    p.write_text(
        "streamers:\n  z:\n    facecam: [[1, 2, 3, 4], [5, 6, 7, 8]]\n  b:\n    facecam: [0, 0, 9, 9]\n"
    )
    z, b = load_layout("z", p), load_layout("b", p)
    assert z.facecam == (1, 2, 3, 4) and z.facecam_alts == [(5, 6, 7, 8)]
    assert b.facecam == (0, 0, 9, 9) and b.facecam_alts == []


def test_game_at_follows_category_changes(tmp_path):
    from clipper.sources import RecordingSource
    from clipper.store import Store

    st = Store(tmp_path / "x.db")
    rid = st.start_recording("evelone2004", tmp_path, 1000.0)
    st.update_recording_meta(rid, "s", "t", "Grand Theft Auto VI", "x")
    st.add_event("evelone2004", 5000.0, "game_change", {"game": "I'm Only Sleeping"})
    st.add_event("evelone2004", 9000.0, "game_change", {"game": "Grand Theft Auto VI"})
    src = RecordingSource(st, rid)
    assert src.game_at(2000) == "Grand Theft Auto VI"
    assert src.game_at(6000) == "I'm Only Sleeping"
    assert src.game_at(9500) == "Grand Theft Auto VI"


def test_locate_cam_finds_the_still_rectangle_around_the_face(monkeypatch):
    import numpy as np

    from clipper.render import facecam

    rng = np.random.default_rng(0)
    room = np.tile(np.linspace(150, 210, 450, dtype=np.uint8), (300, 1))  # still, smooth cam
    frames = []
    for _ in range(7):  # the game around it changes every frame
        f = rng.integers(0, 255, (1080, 1920), dtype=np.uint8)
        f = (f // 64 * 64).astype(np.uint8)
        f[300:600, 1466:1916] = room
        frames.append(f)
    monkeypatch.setattr(facecam, "_face_cluster", lambda fr, need: (1690.0, 450.0, 110.0))
    box, why = facecam.locate_cam(frames, (1460, 384, 460, 306))
    assert box is not None, why
    x, y, w, h = box
    assert abs(x - 1466) <= 4 and abs(y - 300) <= 4 and abs(w - 450) <= 8 and abs(h - 300) <= 8
