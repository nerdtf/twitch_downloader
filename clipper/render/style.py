"""Visual styles for rendered clips (owner 2026-11-20: try designs on a few clips first).

A style only adds looks on top of the normal render: colour grade, subtitle look, a zoom "punch"
on the moment, a white flash at the start, a progress bar, a streamer tag and the hook sticker.
`STYLES["base"]` is exactly the current render. Previews: `python -m clipper.render.variants`.
"""

from dataclasses import dataclass, field

from clipper.render.subtitles import SubStyle

GREEN = (0, 230, 118, 255)
YELLOW = (255, 214, 0, 255)


@dataclass(frozen=True)
class Style:
    name: str
    label: str
    desc: str
    sub: SubStyle = field(default_factory=SubStyle)
    grade: str = ""  # ffmpeg filters applied to the whole frame
    punch: float = 0.0  # zoom-in amount at the moment (0.12 = +12 %)
    flash: bool = False  # white flash in the first 0.3 s
    progress: str = ""  # progress bar colour (ffmpeg colour), "" = none
    tag: bool = False  # small "twitch.tv/<login>" pill
    hook: str = "classic"  # classic (white box) | sticker (yellow, tilted) | none


GRADE = "eq=contrast=1.07:saturation=1.22:gamma=0.98,unsharp=5:5:0.55"
STYLES = {
    s.name: s
    for s in (
        Style(
            "base", "A · как сейчас", "Текущий рендер без изменений (для сравнения).", hook="none"
        ),
        Style(
            "clean",
            "B · чистый",
            "Цветокор (контраст, насыщенность, резкость), крупнее сабы с «попом» слова, "
            "зелёная подсветка, белый хук на 3 с, полоса прогресса снизу.",
            sub=SubStyle(size=84, highlight=GREEN, stroke=7, pop=0.18, max_words=2),
            grade=GRADE,
            progress="0x00E676",
        ),
        Style(
            "hook",
            "C · хук-стикер",
            "Как B + жёлтый наклонный стикер-хук на 3 с, плашка twitch.tv/<стример>, "
            "текущее слово в жёлтой плашке.",
            sub=SubStyle(size=84, stroke=7, pop=0.18, box=YELLOW, max_words=2),
            grade=GRADE,
            progress="0xFFD600",
            tag=True,
            hook="sticker",
        ),
        Style(
            "punch",
            "D · панч",
            "Как C + вспышка в начале, зум-панч на самом моменте, виньетка и лёгкое зерно.",
            sub=SubStyle(size=84, stroke=7, pop=0.22, box=YELLOW, max_words=2),
            grade=GRADE + ",vignette=PI/5,noise=alls=5:allf=t",
            punch=0.12,
            flash=True,
            progress="0xFFD600",
            tag=True,
            hook="sticker",
        ),
    )
}
