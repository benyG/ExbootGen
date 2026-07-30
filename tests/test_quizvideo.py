import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import quizvideo  # noqa: E402


QUESTION = {
    "id": 42,
    "cert_id": 7,
    "cert_name": "CISSP",
    "text": "Which control best mitigates a privileged insider exfiltrating data over an encrypted channel?",
    "options": [
        {"text": "Full-disk encryption on every endpoint", "isok": False},
        {"text": "Separation of duties with access reviews", "isok": True},
        {"text": "A longer password policy", "isok": False},
    ],
    "note": "Encryption protects the data in transit, not from the insider allowed to read it.",
}


def kinds(storyboard):
    return [frame.kind for frame in storyboard]


def test_the_storyboard_runs_hook_question_countdown_answer_cta():
    board = quizvideo.build_storyboard(QUESTION)

    assert kinds(board) == ["hook", "question", "countdown", "countdown", "countdown", "answer", "cta"]


def test_the_options_are_lettered_in_order():
    board = quizvideo.build_storyboard(QUESTION)

    assert list(board[1].lines) == [
        "A. Full-disk encryption on every endpoint",
        "B. Separation of duties with access reviews",
        "C. A longer password policy",
    ]


def test_the_answer_frame_names_the_correct_option():
    board = quizvideo.build_storyboard(QUESTION)
    answer = board[-2]

    assert answer.title == "B. Separation of duties with access reviews"
    assert answer.highlight == 1


def test_the_explanation_is_shown_and_lengthens_the_answer_frame():
    with_note = quizvideo.build_storyboard(QUESTION)[-2]
    without_note = quizvideo.build_storyboard({**QUESTION, "note": ""})[-2]

    assert with_note.lines == (QUESTION["note"],)
    assert with_note.duration > without_note.duration


def test_the_clip_ends_on_the_link():
    board = quizvideo.build_storyboard(QUESTION, cta_url="https://examboot.net/guide/cissp")

    assert board[-1].footer == "https://examboot.net/guide/cissp"
    assert "CISSP" in board[-1].title


def test_the_countdown_can_be_shortened():
    board = quizvideo.build_storyboard(QUESTION, countdown=1)

    assert kinds(board).count("countdown") == 1


def test_a_longer_question_stays_on_screen_longer():
    short = quizvideo.build_storyboard({**QUESTION, "text": "Which one?"})[1]
    long = quizvideo.build_storyboard(QUESTION)[1]

    assert long.duration > short.duration


def test_the_question_never_stays_up_forever():
    board = quizvideo.build_storyboard({**QUESTION, "text": "Why? " * 30})

    assert board[1].duration <= 12.0


def test_a_question_without_a_correct_answer_is_refused():
    options = [{"text": "One", "isok": False}, {"text": "Two", "isok": False}]

    with pytest.raises(quizvideo.QuizVideoError):
        quizvideo.build_storyboard({**QUESTION, "options": options})


def test_a_question_with_a_single_option_is_refused():
    with pytest.raises(quizvideo.QuizVideoError):
        quizvideo.build_storyboard({**QUESTION, "options": [{"text": "Only", "isok": True}]})


def test_an_empty_question_is_refused():
    with pytest.raises(quizvideo.QuizVideoError):
        quizvideo.build_storyboard({**QUESTION, "text": "   "})


def test_a_question_too_dense_for_the_card_is_refused():
    crowded = {
        **QUESTION,
        "text": "A multinational organisation is migrating its platform. " * 8,
        "options": [{"text": "An option that goes on and on and on. " * 6, "isok": index == 0} for index in range(4)],
    }

    with pytest.raises(quizvideo.QuizVideoError, match="trop longue"):
        quizvideo.build_storyboard(crowded)


def test_every_frame_of_a_usable_question_fits_the_card():
    board = quizvideo.build_storyboard(QUESTION)

    assert all(quizvideo.fits(frame) for frame in board)


def test_the_frames_are_rendered_vertically(tmp_path):
    from PIL import Image

    board = quizvideo.build_storyboard(QUESTION, cta_url="https://examboot.net")
    paths = quizvideo.render_frames(board, tmp_path / "frames")

    assert len(paths) == len(board)
    with Image.open(paths[1]) as image:
        assert image.size == (quizvideo.WIDTH, quizvideo.HEIGHT)


def test_the_concat_script_holds_every_frame_and_its_duration(tmp_path):
    board = quizvideo.build_storyboard(QUESTION)
    paths = quizvideo.render_frames(board, tmp_path / "frames")

    script = quizvideo._concat_script(paths, board)

    assert script.count("file '") == len(paths) + 1  # the last still is repeated
    assert f"duration {board[0].duration}" in script


def test_the_video_is_assembled_from_the_frames(tmp_path):
    calls = {}

    def runner(command, capture_output=False, text=False):
        calls["command"] = command
        return SimpleNamespace(returncode=0, stderr="")

    board = quizvideo.build_storyboard(QUESTION)
    paths = quizvideo.render_frames(board, tmp_path / "frames")

    output = quizvideo.assemble_video(paths, board, tmp_path / "clip.mp4", runner=runner)

    assert output == tmp_path / "clip.mp4"
    assert calls["command"][0] == "ffmpeg"
    assert "libx264" in calls["command"]


def test_a_failing_ffmpeg_is_reported(tmp_path):
    def runner(command, capture_output=False, text=False):
        return SimpleNamespace(returncode=1, stderr="boom")

    board = quizvideo.build_storyboard(QUESTION)
    paths = quizvideo.render_frames(board, tmp_path / "frames")

    with pytest.raises(quizvideo.QuizVideoError, match="ffmpeg"):
        quizvideo.assemble_video(paths, board, tmp_path / "clip.mp4", runner=runner)


def test_a_storyboard_that_does_not_match_the_frames_is_refused(tmp_path):
    board = quizvideo.build_storyboard(QUESTION)
    paths = quizvideo.render_frames(board, tmp_path / "frames")

    with pytest.raises(quizvideo.QuizVideoError):
        quizvideo.assemble_video(paths[:2], board, tmp_path / "clip.mp4", runner=lambda *a, **k: None)


def test_building_a_clip_reports_its_length(tmp_path):
    def runner(command, capture_output=False, text=False):
        return SimpleNamespace(returncode=0, stderr="")

    built = quizvideo.build_quiz_video(QUESTION, tmp_path / "clip.mp4", cta_url="https://examboot.net", runner=runner)

    assert built["duration"] == round(sum(frame.duration for frame in built["storyboard"]), 1)
    assert len(built["frames"]) == len(built["storyboard"])


def test_the_closing_link_carries_the_channel_and_the_question():
    link = quizvideo.video_link("https://examboot.net/guide/cissp", channel="tiktok", content="42")

    assert "utm_source=tiktok" in link
    assert "utm_medium=video" in link
    assert "utm_campaign=quiz-video" in link
    assert "utm_content=42" in link
