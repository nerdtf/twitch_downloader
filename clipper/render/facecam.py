"""Choose split vs fit_blur per clip: split only when a face is really inside the configured
facecam box (scenes change: IRL, BRB, full-screen cam, a guest's cam...).

Uses OpenCV's Haar face detector on a few frames of the clip — cheap (~0.1 s per frame).
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
    hits = 0
    for i in range(samples):
        at = offset + duration * (i + 0.5) / samples
        frame = grab_frame(video, at, info["width"], info["height"])
        hits += frame is not None and face_in(frame, lay.facecam)
    need = max(2, round(samples * 0.4))  # a stray false positive must not flip the layout
    if hits >= need:
        return replace(lay, kind="split"), f"face in facecam box {hits}/{samples}"
    return fit, f"no face in facecam box ({hits}/{samples})"
