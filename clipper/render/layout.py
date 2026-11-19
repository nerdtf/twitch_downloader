"""Per-streamer layout config -> ffmpeg filtergraph for a 1080x1920 vertical video."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from clipper.config import REPO_ROOT

OUT_W, OUT_H = 1080, 1920
DEFAULT_PATH = REPO_ROOT / "config/layouts.yaml"

Rect = tuple[int, int, int, int]


@dataclass
class Layout:
    kind: str = "fit_blur"
    facecam: Rect | None = None
    content: Rect | None = None
    blur: list[Rect] = field(default_factory=list)
    content_zoom: float = 1.0
    facecam_alts: list[Rect] = field(default_factory=list)  # other known cam spots (auto picks)


def load_layout(login: str, path: Path = DEFAULT_PATH) -> Layout:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    merged = {**(raw.get("defaults") or {}), **((raw.get("streamers") or {}).get(login) or {})}
    fc = merged.get("facecam") or []
    # facecam: [x, y, w, h], or a list of them when the streamer moves the cam between scenes
    cams = (
        [tuple(r) for r in fc]
        if fc and isinstance(fc[0], list | tuple)
        else ([tuple(fc)] if fc else [])
    )
    lay = Layout(
        kind=merged.get("layout", "fit_blur"),
        facecam=cams[0] if cams else None,
        facecam_alts=cams[1:],
        content=tuple(merged["content"]) if merged.get("content") else None,
        blur=[tuple(r) for r in merged.get("blur") or []],
        content_zoom=float(merged.get("content_zoom", 1.0)),
    )
    if lay.kind == "split" and lay.facecam is None:
        lay.kind = "fit_blur"  # cannot split without a facecam rect
    return lay


def _even(v: float) -> int:
    return int(v) // 2 * 2


def _scale_rect(r: Rect, sx: float, sy: float) -> Rect:
    return (_even(r[0] * sx), _even(r[1] * sy), _even(r[2] * sx), _even(r[3] * sy))


def _avoid(cx: int, cw: int, fx: int, fw: int, src_w: int) -> int:
    """Shift a centred crop [cx, cx+cw) so it does not include the webcam [fx, fx+fw)."""
    if cx + cw <= fx or cx >= fx + fw:
        return cx
    if fx + fw / 2 < src_w / 2:  # cam on the left -> move right
        return _even(min(fx + fw, src_w - cw))
    return _even(max(fx - cw, 0))  # cam on the right -> move left


def _cam_h(lay: Layout, src_w: int, src_h: int) -> int:
    fw, fh = lay.facecam[2] * src_w / 1920, lay.facecam[3] * src_h / 1080
    return _even(OUT_W * fh / fw)


def sub_center_y(lay: Layout, src_w: int, src_h: int) -> int:
    """Vertical centre of the subtitle line: just under the streamer's webcam (split) or just
    under the 16:9 frame (fit_blur)."""
    if lay.kind == "split" and lay.facecam:
        return _cam_h(lay, src_w, src_h) + 75
    frame_h = OUT_W * src_h / src_w * lay.content_zoom
    return int((OUT_H + frame_h) / 2 + 75)


def hook_y(lay: Layout, src_w: int, src_h: int) -> int:
    """Top of the hook banner: never over the face or the subtitles."""
    if lay.kind == "split" and lay.facecam:
        return _cam_h(lay, src_w, src_h) + 180  # below the subtitle line
    return 150  # fit_blur: blurred area above the frame


def filtergraph(lay: Layout, src_w: int, src_h: int, inp: str = "0:v", out: str = "vout") -> str:
    """Filtergraph from the source video stream to a 1080x1920 stream labelled [out]."""
    sx, sy = src_w / 1920, src_h / 1080  # config rects are in 1920x1080 space
    parts: list[str] = []
    cur = inp
    for i, r in enumerate(lay.blur):
        x, y, w, h = _scale_rect(r, sx, sy)
        parts.append(f"[{cur}]split[b{i}a][b{i}b]")
        parts.append(f"[b{i}b]crop={w}:{h}:{x}:{y},gblur=sigma=25[b{i}c]")
        parts.append(f"[b{i}a][b{i}c]overlay={x}:{y}[b{i}]")
        cur = f"b{i}"

    if lay.kind == "split":
        fx, fy, fw, fh = _scale_rect(lay.facecam, sx, sy)
        cam_h = _even(OUT_W * fh / fw)
        content_h = OUT_H - cam_h
        if lay.content:
            cx, cy, cw, ch = _scale_rect(lay.content, sx, sy)
        else:
            cx, cy, cw, ch = 0, 0, src_w, src_h
        # crop the content rect to the 1080:content_h aspect around its centre
        target = OUT_W / content_h
        if cw / ch > target:
            nw = _even(ch * target)
            cx, cw = cx + (cw - nw) // 2, nw
            if not lay.content:  # whole frame: slide the crop sideways off the webcam
                cx = _avoid(cx, cw, fx, fw, src_w)
        else:
            nh = _even(cw / target)
            cy, ch = cy + (ch - nh) // 2, nh
        parts.append(f"[{cur}]split[sa][sb]")
        parts.append(f"[sa]crop={fw}:{fh}:{fx}:{fy},scale={OUT_W}:{cam_h}[cam]")
        parts.append(f"[sb]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{content_h}[content]")
        parts.append(f"[cam][content]vstack,setsar=1[{out}]")
    else:
        fg_w = _even(OUT_W * lay.content_zoom)
        parts.append(f"[{cur}]split[fa][fb]")
        parts.append(
            f"[fa]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},"
            "gblur=sigma=40,eq=brightness=-0.15[bg]"
        )
        parts.append(f"[fb]scale={fg_w}:-2,crop=min(iw\\,{OUT_W}):ih[fg]")
        parts.append(f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")
    return ";".join(parts)
