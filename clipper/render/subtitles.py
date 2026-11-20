"""Word-level subtitles and hook text rendered as transparent PNGs (this ffmpeg has no libass).

Subtitles become ONE extra video input: an ffconcat list of PNG frames with durations, overlaid
at a fixed position. The current word is highlighted (karaoke style).
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from clipper.judge.transcribe import Word

FONT = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
BAND_W, BAND_H = 1080, 360
WHITE, YELLOW, BLACK = (255, 255, 255, 255), (255, 214, 0, 255), (0, 0, 0, 255)


@dataclass(frozen=True)
class SubStyle:
    """Look of the word-by-word subtitles. Defaults = the original style."""

    size: int = 72
    highlight: tuple = YELLOW
    stroke: int = 6
    pop: float = 0.0  # >0: the current word starts this much bigger for POP_S (karaoke "pop")
    box: tuple | None = None  # highlight the current word with a rounded box of this colour
    max_words: int = 3


POP_S = 0.09


@dataclass(frozen=True)
class Chunk:
    words: tuple[Word, ...]
    start: float
    end: float


def chunk_words(
    words: list[Word], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.6
) -> list[Chunk]:
    chunks: list[list[Word]] = []
    for w in words:
        if not w.text.strip():
            continue
        cur = chunks[-1] if chunks else None
        if (
            cur is None
            or len(cur) >= max_words
            or len(" ".join(x.text.strip() for x in cur + [w])) > max_chars
            or w.start - cur[-1].end > max_gap
        ):
            chunks.append([w])
        else:
            cur.append(w)
    return [Chunk(tuple(c), c[0].start, c[-1].end) for c in chunks]


def _fit_font(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int
) -> ImageFont.FreeTypeFont:
    while size > 30:
        font = ImageFont.truetype(FONT, size)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 6
    return ImageFont.truetype(FONT, size)


def render_chunk(
    chunk: Chunk, highlight: int | None, out: Path, size: int = 72, style: SubStyle | None = None,
    grow: float = 1.0,
) -> Path:  # fmt: skip
    st = style or SubStyle(size=size)
    img = Image.new("RGBA", (BAND_W, BAND_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    words = [w.text.strip().upper().strip('«»"„“”').rstrip(".,:;") or w.text.strip()
             for w in chunk.words]  # fmt: skip
    font = _fit_font(d, " ".join(words), BAND_W - 80, st.size)
    big = ImageFont.truetype(FONT, max(10, round(font.size * grow)))
    space = d.textlength(" ", font=font)
    total = sum(d.textlength(w, font=font) for w in words) + space * (len(words) - 1)
    x, y = (BAND_W - total) / 2, BAND_H / 2
    for i, w in enumerate(words):
        cur = i == highlight
        f = big if cur and grow != 1.0 else font
        wl = d.textlength(w, font=font)
        cx = x + wl / 2
        if cur and st.box:
            bw, bh = d.textlength(w, font=f) + 34, f.size * 1.25
            d.rounded_rectangle([cx - bw / 2, y - bh / 2, cx + bw / 2, y + bh / 2], radius=18,
                                fill=st.box)  # fmt: skip
        fill = (BLACK if st.box else st.highlight) if cur else WHITE
        d.text((cx, y), w, font=f, fill=fill, anchor="mm",
               stroke_width=0 if (cur and st.box) else st.stroke, stroke_fill=BLACK)  # fmt: skip
        x += wl + space
    img.save(out)
    return out


def render_blank(out: Path) -> Path:
    Image.new("RGBA", (BAND_W, BAND_H), (0, 0, 0, 0)).save(out)
    return out


def build_subtitle_track(
    words: list[Word], duration: float, workdir: Path, style: SubStyle | None = None
) -> Path:
    """Write PNG frames + an ffconcat list covering [0, duration]; return the list path."""
    workdir.mkdir(parents=True, exist_ok=True)
    blank = render_blank(workdir / "blank.png")
    entries: list[tuple[Path, float]] = []
    t = 0.0
    st = style or SubStyle()
    for ci, ch in enumerate(chunk_words(words, max_words=st.max_words)):
        if ch.start > t + 0.02:
            entries.append((blank, ch.start - t))
            t = ch.start
        for wi, w in enumerate(ch.words):
            end = ch.words[wi + 1].start if wi + 1 < len(ch.words) else max(ch.end, w.end)
            if end - t <= 0.01:
                continue
            if st.pop > 0 and end - t > POP_S * 2:
                png = render_chunk(ch, wi, workdir / f"c{ci:03d}_{wi}p.png", style=st,
                                   grow=1 + st.pop)  # fmt: skip
                entries.append((png, POP_S))
                t += POP_S
            png = render_chunk(ch, wi, workdir / f"c{ci:03d}_{wi}.png", style=st)
            entries.append((png, end - t))
            t = end
        if t >= duration:
            break
    if t < duration:
        entries.append((blank, duration - t))
    lines = ["ffconcat version 1.0"]
    for p, d in entries:
        lines += [f"file '{p.resolve()}'", f"duration {max(d, 0.01):.3f}"]
    lines.append(f"file '{entries[-1][0].resolve()}'")  # concat demuxer needs the last file twice
    listing = workdir / "subs.ffconcat"
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return listing


def render_hook(
    text: str, out: Path, size: int = 74, fill=WHITE, ink=BLACK, angle: float = 0.0
) -> Path:
    """Hook banner (top of the video, first seconds): white box, black text, up to 2 lines."""
    img = Image.new("RGBA", (BAND_W, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    words = text.strip().split()
    while True:  # shrink until the whole hook fits in 2 lines (never drop words)
        font = ImageFont.truetype(FONT, size)
        lines, cur = [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if d.textlength(trial, font=font) > BAND_W - 160 and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
        if len(lines) <= 2 or size <= 40:
            break
        size -= 6
    lines = lines[:2]
    line_h = size + 18
    box_h = line_h * len(lines) + 40
    box_w = max(d.textlength(line, font=font) for line in lines) + 70
    x0, y0 = (BAND_W - box_w) / 2, (300 - box_h) / 2
    d.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=28, fill=fill)
    for i, line in enumerate(lines):
        d.text(
            (BAND_W / 2, y0 + 20 + line_h * i + line_h / 2),
            line,
            font=font,
            fill=ink,
            anchor="mm",
        )
    if angle:
        img = img.rotate(angle, resample=Image.BICUBIC)
    img.save(out)
    return out


def render_tag(text: str, out: Path, size: int = 34) -> Path:
    """Small semi-transparent pill, e.g. "twitch.tv/zloyn"."""
    font = ImageFont.truetype(FONT, size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    w = int(probe.textlength(text, font=font)) + 44
    img = Image.new("RGBA", (w, size + 26), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, size + 25], radius=(size + 26) // 2, fill=(0, 0, 0, 150))
    d.text((w / 2, (size + 26) / 2), text, font=font, fill=WHITE, anchor="mm")
    img.save(out)
    return out
