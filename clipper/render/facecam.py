"""Choose split vs fit_blur per clip and find where the webcam is on this clip.

Streamers move their cam (zloyn: 3 spots in 2 days), so every clip looks for it on its own frames:
a face that repeats across the sampled frames, then the cam's rectangle around it — the edges that
stay still while the game moves (Canny on ~7 frames spread over the clip). The configured
`facecam` boxes give the expected size and are the fallback. No face -> fit_blur (BRB, IRL, empty
chair...). OpenCV Haar detector, ~0.5 s per frame at 1080p.
"""

import logging
import subprocess
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from clipper.media.ffmpeg import probe_video
from clipper.render.layout import Layout

log = logging.getLogger(__name__)
IRL_GAMES = {"just chatting", "irl", "travel & outdoors", "talk shows & podcasts", "music"}
_CASCADE = None
_ALT = None


def _detector():
    global _CASCADE
    if _CASCADE is None:
        _CASCADE = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
    return _CASCADE


def grab_frame(video: Path, at: float, w: int, h: int) -> np.ndarray | None:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(at, 0):.2f}",
           "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]  # fmt: skip
    out = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    if len(out) < w * h:
        return None
    return np.frombuffer(out[: w * h], dtype=np.uint8).reshape(h, w)


def face_in(gray: np.ndarray, rect: tuple[int, int, int, int]) -> bool:
    """rect in 1920x1080 space; gray is the full frame at source resolution."""
    h, w = gray.shape
    sx, sy = w / 1920, h / 1080
    x, y, rw, rh = int(rect[0] * sx), int(rect[1] * sy), int(rect[2] * sx), int(rect[3] * sy)
    roi = gray[y : y + rh, x : x + rw]
    if roi.size == 0:
        return False
    if roi.shape[0] < 240:  # small cams: upscale so the detector's minimum face size fits
        f = 240 / roi.shape[0]
        roi = cv2.resize(roi, None, fx=f, fy=f)
    roi = cv2.equalizeHist(roi)
    min_side = max(int(roi.shape[0] * 0.18), 24)
    faces = _detector().detectMultiScale(roi, 1.1, 4, minSize=(min_side, min_side))
    return len(faces) > 0


REF = (1920, 1080)  # layout coordinates are in this frame size


def _static_edges(frames: list[np.ndarray]) -> np.ndarray:
    hits = [cv2.Canny(cv2.GaussianBlur(f, (3, 3), 0), 15, 45) > 0 for f in frames]
    e = (np.mean(hits, 0) >= 0.7).astype(np.uint8)
    return cv2.dilate(e, np.ones((3, 3), np.uint8)) > 0


def _alt_detector():
    global _ALT
    if _ALT is None:  # alt2 also finds faces under headbands/hoods where the default misses
        _ALT = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml")
    return _ALT


def _face_cluster(frames: list[np.ndarray], need: int) -> tuple[float, float, float] | None:
    """Median (cx, cy, width) of the face seen at the same place in >= `need` frames."""
    found = []  # (frame index, cx, cy, w)
    for i, f in enumerate(frames):
        g = cv2.equalizeHist(f)
        for det, neighbours in ((_detector(), 5), (_alt_detector(), 3)):
            for x, y, w, h in det.detectMultiScale(
                g, 1.1, neighbours, minSize=(40, 40), maxSize=(320, 320)
            ):
                found.append((i, x + w / 2, y + h / 2, float(w)))
    best: list = []
    best_frames = 0
    for _i, cx, cy, _w in found:
        near = [f for f in found if abs(f[1] - cx) < 60 and abs(f[2] - cy) < 60]
        n_frames = len({f[0] for f in near})
        if n_frames > best_frames:
            best, best_frames = near, n_frames
    if best_frames < need:
        return None
    m = np.median(np.array([f[1:] for f in best]), 0)
    return float(m[0]), float(m[1]), float(m[2])


def locate_cam(
    frames: list[np.ndarray], ref: tuple[int, int, int, int], need: int = 2
) -> tuple[tuple[int, int, int, int] | None, str]:
    """Webcam rectangle (x, y, w, h) on 1920x1080 gray frames; `ref` gives the expected size."""
    face = _face_cluster(frames, need)
    if face is None:
        return None, "no face"
    fx, fy, fw = face
    big_h, big_w = frames[0].shape
    W, H = ref[2], ref[3]
    e = _static_edges(frames)
    vcum = np.vstack([np.zeros((1, big_w)), np.cumsum(e, 0)])
    hcum = np.hstack([np.zeros((big_h, 1)), np.cumsum(e, 1)])

    def vsup(x: int, y0: float, y1: float) -> float:  # share of a vertical line on static edges
        if x <= 2 or x >= big_w - 3:
            return 1.0  # the frame border counts as an edge
        a, b = max(int(y0), 0), min(int(y1), big_h)
        return float(vcum[b, x] - vcum[a, x]) / max(b - a, 1)

    def hsup(y: int, x0: float, x1: float) -> float:
        if y <= 2 or y >= big_h - 3:
            return 1.0
        a, b = max(int(x0), 0), min(int(x1), big_w)
        return float(hcum[y, b] - hcum[y, a]) / max(b - a, 1)

    def strongest(lo: float, hi: float, score, edge: int | None) -> list[int]:
        ranked = sorted(((score(c), c) for c in range(max(int(lo), 0), int(hi))), reverse=True)
        out: list[int] = []
        for sc, c in ranked:
            if sc <= 0.2 or len(out) == 10:
                break
            if all(abs(c - o) > 6 for o in out):
                out.append(c)
        if edge is not None:
            out.append(edge)
        return out

    near_l, near_r = fx - 1.2 * W <= 0, fx + 1.2 * W >= big_w
    near_t, near_b = fy - 1.2 * H <= 0, fy + 1.2 * H >= big_h
    lefts = strongest(
        fx - 1.2 * W,
        fx - 0.6 * fw,
        lambda x: vsup(x, fy - 0.4 * H, fy + 0.4 * H),
        0 if near_l else None,
    )
    rights = strongest(
        fx + 0.6 * fw,
        min(big_w, fx + 1.2 * W),
        lambda x: vsup(x, fy - 0.4 * H, fy + 0.4 * H),
        big_w - 1 if near_r else None,
    )
    tops = strongest(
        fy - 1.2 * H,
        fy - 0.6 * fw,
        lambda y: hsup(y, fx - 0.5 * W, fx + 0.5 * W),
        0 if near_t else None,
    )
    bottoms = strongest(fy + 0.6 * fw, min(big_h, fy + 1.2 * H),
                        lambda y: hsup(y, fx - 0.5 * W, fx + 0.5 * W), big_h - 1 if near_b else None)  # fmt: skip
    best, box = 0.0, None
    for left in lefts:
        for right in rights:
            if not 0.6 * W <= right - left <= 1.5 * W:
                continue
            for top in tops:
                for bot in bottoms:
                    if not 0.6 * H <= bot - top <= 1.5 * H:
                        continue
                    sides = (vsup(left, top, bot), vsup(right, top, bot),
                             hsup(top, left, right), hsup(bot, left, right))  # fmt: skip
                    if min(sides) < 0.3:
                        continue
                    off_size = abs(np.log((right - left) / W)) + abs(np.log((bot - top) / H))
                    score = sum(sides) / 4 - 0.15 * off_size
                    if score > best:
                        best, box = score, (left, top, right - left + 1, bot - top + 1)
    if box is None or best < 0.5:
        return None, f"face at {int(fx)},{int(fy)} but no cam border (best {best:.2f})"
    x, y, w, h = box
    return (min(x, big_w - w), min(y, big_h - h), w, h), f"edges {best:.2f}"


def resolve_layout(
    lay: Layout, video: Path, offset: float, duration: float, game: str = "", samples: int = 7
) -> tuple[Layout, str]:
    """(layout to use, reason). `kind: auto` -> split if a face is in the facecam box."""
    if lay.kind != "auto":
        return lay, f"configured {lay.kind}"
    fit = replace(lay, kind="fit_blur")
    if not lay.facecam:
        return fit, "no facecam configured"
    if (game or "").strip().lower() in IRL_GAMES:
        return fit, f"IRL category ({game})"
    info = probe_video(video)
    boxes = [lay.facecam, *lay.facecam_alts]
    hits = [0] * len(boxes)
    frames = []
    for i in range(samples):
        at = offset + duration * (i + 0.5) / samples
        frame = grab_frame(video, at, info["width"], info["height"])
        if frame is None:
            continue
        for k, box in enumerate(boxes):
            hits[k] += face_in(frame, box)
        if frame.shape[::-1] != REF:
            frame = cv2.resize(frame, REF)
        frames.append(frame)
    need = max(2, round(samples * 0.4))  # a stray false positive must not flip the layout
    if len(frames) >= 3:
        found, why = locate_cam(frames, tuple(lay.facecam), need)
        if found:
            return replace(lay, kind="split", facecam=tuple(found)), (
                f"cam found at {list(found)} ({why})"
            )
        log.info("cam search: %s; trying configured boxes", why)
    best = max(range(len(boxes)), key=lambda k: hits[k])
    where = f" (cam spot {best + 1}/{len(boxes)})" if len(boxes) > 1 else ""
    if hits[best] >= need:
        return replace(lay, kind="split", facecam=boxes[best]), (
            f"face in facecam box {hits[best]}/{samples}{where}"
        )
    return fit, f"no face in facecam box ({hits[best]}/{samples})"
