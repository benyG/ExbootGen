"""Vertical quiz videos, drawn from the question bank the platform already has.

A question, its options, a three-second countdown, the answer and why — the
format that travels on Shorts, Reels and TikTok. No video model is involved and
nothing is paid for per clip: the frames are drawn with Pillow and ffmpeg
strings them together, so a video costs a few seconds of CPU. The bank is the
content; this only stages it.

The clip ends on a tagged link, which is the whole point: a view that never
reaches the platform is a view that earns nothing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from attribution import tag_link

BASE_DIR = Path(__file__).resolve().parent

#: Vertical, the only aspect ratio the short-form feeds reward.
WIDTH = 1080
HEIGHT = 1920

MARGIN = 90

#: The card the text lives in, and the band inside it the text may occupy.
CARD_TOP = 260
CARD_BOTTOM = HEIGHT - 320
CONTENT_TOP = 360
CONTENT_BOTTOM = CARD_BOTTOM - 60

MIN_TITLE_SIZE = 34
MIN_LINE_SIZE = 32

BACKGROUND = "#0f172a"
CARD = "#1e293b"
ACCENT = "#22c55e"
TEXT = "#e2e8f0"
MUTED = "#94a3b8"

DEFAULT_CAMPAIGN = "quiz-video"

#: Letters shown against each option, in order.
LETTERS = ("A", "B", "C", "D", "E", "F")

FONT_CANDIDATES = (
    ("Poppins-Regular.ttf", "Poppins-Bold.ttf"),
    ("Montserrat-Regular.ttf", "Montserrat-Bold.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
)
FONT_SEARCH_PATHS = (
    BASE_DIR / "fonts",
    Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("/usr/share/fonts/truetype"),
    Path("/usr/share/fonts/truetype/dejavu"),
)


class QuizVideoError(RuntimeError):
    """Raised when a clip cannot be built."""


@dataclass
class Frame:
    """One still of the storyboard, with how long it stays on screen."""

    kind: str
    duration: float
    eyebrow: str = ""
    title: str = ""
    lines: Sequence[str] = field(default_factory=tuple)
    footer: str = ""
    highlight: Optional[int] = None


def build_storyboard(question: dict, cta_url: str = "", countdown: int = 3) -> list[Frame]:
    """Turn one question into the frames of a clip.

    Pure on purpose: the storyboard is what decides whether a clip is watchable,
    so it must be readable and testable without rendering a single pixel.
    """

    text = (question.get("text") or "").strip()
    if not text:
        raise QuizVideoError("La question est vide.")

    options = [option for option in (question.get("options") or []) if (option.get("text") or "").strip()]
    if len(options) < 2:
        raise QuizVideoError("Une question de quiz a besoin d'au moins deux réponses.")

    correct = next((index for index, option in enumerate(options) if option.get("isok")), None)
    if correct is None:
        raise QuizVideoError("Aucune bonne réponse n'est marquée sur cette question.")

    cert = (question.get("cert_name") or "").strip()
    labelled = [f"{LETTERS[index]}. {option['text'].strip()}" for index, option in enumerate(options)]

    frames = [
        Frame(kind="hook", duration=2.0, eyebrow=cert, title=_hook_title(cert), footer="👇"),
        Frame(
            kind="question",
            duration=_reading_time(text, labelled),
            eyebrow=cert,
            title=text,
            lines=labelled,
        ),
    ]

    for remaining in range(max(countdown, 0), 0, -1):
        frames.append(Frame(kind="countdown", duration=1.0, eyebrow=cert, title=str(remaining)))

    note = (question.get("note") or "").strip()
    frames.append(
        Frame(
            kind="answer",
            duration=6.0 if note else 4.0,
            eyebrow="Answer",
            title=labelled[correct],
            lines=(note,) if note else (),
            highlight=correct,
        )
    )

    frames.append(
        Frame(
            kind="cta",
            duration=3.0,
            eyebrow=cert,
            title=_cta_title(cert),
            footer=(cta_url or "").strip(),
        )
    )

    # A question bank is not written for a phone screen. Refusing the ones that
    # cannot be read in six seconds is better than shipping a clip whose answer
    # runs off the bottom of the frame — the console simply picks another.
    for frame in frames:
        if not fits(frame):
            raise QuizVideoError(
                "Cette question est trop longue pour une vidéo verticale : choisissez-en une autre."
            )

    return frames


def render_frames(storyboard: Sequence[Frame], directory: Path) -> list[Path]:
    """Draw every frame as a PNG and return the paths, in order."""

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    paths = []
    for index, frame in enumerate(storyboard):
        path = directory / f"frame-{index:02d}.png"
        _render_frame(frame).save(path, "PNG")
        paths.append(path)

    return paths


def assemble_video(
    frames: Sequence[Path],
    storyboard: Sequence[Frame],
    output: Path,
    runner: Optional[Callable] = None,
    fps: int = 30,
) -> Path:
    """String the frames into an MP4 with ffmpeg.

    ffmpeg is a system binary, like the tesseract and poppler this console
    already relies on; its absence is reported plainly rather than guessed at.
    """

    if len(frames) != len(storyboard):
        raise QuizVideoError("Le nombre d'images ne correspond pas au storyboard.")
    if not frames:
        raise QuizVideoError("Aucune image à assembler.")

    runner = runner or subprocess.run
    if runner is subprocess.run and shutil.which("ffmpeg") is None:
        raise QuizVideoError(
            "ffmpeg est introuvable. Installez-le sur le serveur pour produire les vidéos."
        )

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    concat = output.with_suffix(".txt")
    concat.write_text(_concat_script(frames, storyboard), encoding="utf-8")

    command = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-vf", f"fps={fps},format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-movflags", "+faststart",
        str(output),
    ]

    result = runner(command, capture_output=True, text=True)
    if getattr(result, "returncode", 1) != 0:
        raise QuizVideoError(f"ffmpeg a échoué : {getattr(result, 'stderr', '')[:400]}")

    return output


def build_quiz_video(
    question: dict,
    output: Path,
    cta_url: str = "",
    countdown: int = 3,
    runner: Optional[Callable] = None,
) -> dict:
    """Storyboard, render and assemble one clip in a single call."""

    output = Path(output)
    storyboard = build_storyboard(question, cta_url=cta_url, countdown=countdown)
    frames = render_frames(storyboard, output.parent / f"{output.stem}-frames")
    assemble_video(frames, storyboard, output, runner=runner)

    return {
        "path": output,
        "frames": frames,
        "storyboard": storyboard,
        "duration": round(sum(frame.duration for frame in storyboard), 1),
    }


def video_link(url: str, *, channel: str = "shorts", campaign: Optional[str] = None, content=None) -> str:
    """Tag the link the clip ends on, so a view can be traced to an address."""

    return tag_link(url, channel=channel, campaign=campaign or DEFAULT_CAMPAIGN, content=content)


def build_caption(question: dict, cta_url: str = "") -> str:
    """The text posted alongside the clip.

    Short on purpose: the clip carries the content, the caption only has to
    stop the scroll and say where the answer leads.
    """

    cert = (question.get("cert_name") or "").strip()
    lines = [
        f"{_hook_title(cert)} 👇",
        "",
        "60 seconds, one question, the reasoning behind the answer.",
    ]

    if cta_url.strip():
        lines += ["", cta_url.strip()]

    return "\n".join(lines)


def _reading_time(text: str, options: Sequence[str]) -> float:
    """How long the question stays up: long enough to read, short enough to hold.

    Roughly fifteen characters a second, which is a slow reader on a phone,
    bounded so a one-line question is not left hanging and a dense one is not
    cut off before the countdown.
    """

    characters = len(text) + sum(len(option) for option in options)

    return round(min(max(4.0, 2.0 + characters / 15), 12.0), 1)


def _hook_title(cert: str) -> str:
    return f"Can you answer this {cert} question?" if cert else "Can you answer this question?"


def _cta_title(cert: str) -> str:
    return f"Free {cert} practice test" if cert else "Free practice test"


def _concat_script(frames: Sequence[Path], storyboard: Sequence[Frame]) -> str:
    """The ffmpeg concat demuxer script, one still per requested duration."""

    lines = []
    for path, frame in zip(frames, storyboard):
        lines.append(f"file '{path.resolve().as_posix()}'")
        lines.append(f"duration {frame.duration}")

    # The demuxer drops the last entry's duration, so the final still is repeated.
    lines.append(f"file '{Path(frames[-1]).resolve().as_posix()}'")

    return "\n".join(lines) + "\n"


def _render_frame(frame: Frame) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (MARGIN - 30, CARD_TOP, WIDTH - MARGIN + 30, CARD_BOTTOM),
        radius=48,
        fill=CARD,
    )

    if frame.eyebrow:
        draw.text((MARGIN, 170), frame.eyebrow.upper(), font=_font(46, bold=True), fill=ACCENT)

    if frame.kind == "countdown":
        _draw_centred(draw, frame.title, _font(420, bold=True), ACCENT)
        return image

    title_size, line_size = _layout(draw, frame)
    title_colour = ACCENT if frame.kind == "answer" else TEXT

    cursor = _draw_block(
        draw, frame.title, _font(title_size, bold=True), title_colour, CONTENT_TOP, line_gap=14
    )

    if frame.lines:
        cursor += 60
        for line in frame.lines:
            colour = MUTED if frame.kind == "answer" else TEXT
            cursor = _draw_block(draw, line, _font(line_size), colour, cursor, line_gap=8)
            cursor += 26

    if frame.footer:
        draw.text((MARGIN, HEIGHT - 230), frame.footer, font=_font(46, bold=True), fill=ACCENT)

    return image


def _title_size(frame: Frame) -> int:
    if frame.kind in ("hook", "cta"):
        return 86
    if len(frame.title) > 220:
        return 48

    return 58 if len(frame.title) > 120 else 66


def _layout(draw, frame: Frame) -> tuple[int, int]:
    """The largest sizes at which this frame's text still fits inside the card.

    A question bank is not written for a 1080x1920 card: some questions carry
    four long options. Shrinking beats overflowing off the bottom of the frame,
    where nobody would ever see the answer.
    """

    title_size = _title_size(frame)
    line_size = 52

    while True:
        if CONTENT_TOP + _content_height(draw, frame, title_size, line_size) <= CONTENT_BOTTOM:
            break
        if title_size <= MIN_TITLE_SIZE and line_size <= MIN_LINE_SIZE:
            break
        title_size = max(MIN_TITLE_SIZE, title_size - 4)
        line_size = max(MIN_LINE_SIZE, line_size - 3)

    return title_size, line_size


def fits(frame: Frame) -> bool:
    """Whether this frame can be shown without running off the bottom of the card."""

    if frame.kind == "countdown":
        return True

    height = _content_height(_measuring_draw(), frame, MIN_TITLE_SIZE, MIN_LINE_SIZE)

    return CONTENT_TOP + height <= CONTENT_BOTTOM


def _measuring_draw():
    """A drawing context used only to measure text, never to produce a frame."""

    global _MEASURE_DRAW
    if _MEASURE_DRAW is None:
        _MEASURE_DRAW = ImageDraw.Draw(Image.new("RGB", (WIDTH, HEIGHT)))

    return _MEASURE_DRAW


def _content_height(draw, frame: Frame, title_size: int, line_size: int) -> int:
    height = _block_height(draw, frame.title, _font(title_size, bold=True), line_gap=14)

    if frame.lines:
        height += 60
        for line in frame.lines:
            height += _block_height(draw, line, _font(line_size), line_gap=8) + 26

    return height


def _block_height(draw, text: str, font, line_gap: int) -> int:
    return len(_wrap(draw, text, font, WIDTH - 2 * MARGIN)) * (_line_height(font) + line_gap)


def _draw_block(draw, text: str, font, colour: str, top: int, line_gap: int = 10) -> int:
    """Word-wrap ``text`` into the card and return the new vertical cursor."""

    max_width = WIDTH - 2 * MARGIN
    line_height = _line_height(font)

    for line in _wrap(draw, text, font, max_width):
        draw.text((MARGIN, top), line, font=font, fill=colour)
        top += line_height + line_gap

    return top


def _draw_centred(draw, text: str, font, colour: str) -> None:
    width, height = _measure(draw, text, font)
    draw.text(((WIDTH - width) / 2, (HEIGHT - height) / 2), text, font=font, fill=colour)


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return []

    lines = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure(draw, candidate, font)[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)

    return lines


def _measure(draw, text: str, font) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)

    return right - left, bottom - top


def _line_height(font) -> int:
    ascent, descent = font.getmetrics() if hasattr(font, "getmetrics") else (font.size, 0)

    return int(ascent + descent)


_FONT_CACHE: dict = {}
_MEASURE_DRAW = None


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    path = _font_file(bold)
    if path is not None:
        font = ImageFont.truetype(str(path), size)
    else:  # pragma: no cover - only on a host without any of the candidates
        font = ImageFont.load_default(size=size)

    _FONT_CACHE[key] = font

    return font


def _font_file(bold: bool) -> Optional[Path]:
    for regular_name, bold_name in FONT_CANDIDATES:
        found = _find_font(bold_name if bold else regular_name)
        if found:
            return found
        found = _find_font(regular_name)
        if found:
            return found

    return None


def _find_font(filename: str) -> Optional[Path]:
    for base in FONT_SEARCH_PATHS:
        if not base.exists():
            continue
        candidate = base / filename
        if candidate.exists():
            return candidate
        for match in base.rglob(filename):
            return match

    return None
