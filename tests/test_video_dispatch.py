import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as admin_app  # noqa: E402
import db  # noqa: E402
import quizvideo  # noqa: E402
import videopub  # noqa: E402


QUESTION = {
    "id": 42,
    "cert_id": 7,
    "cert_name": "CISSP",
    "text": "Which control best mitigates a privileged insider exfiltrating data?",
    "options": [
        {"text": "Full-disk encryption", "isok": False},
        {"text": "Separation of duties", "isok": True},
    ],
    "note": "Encryption protects the data in transit, not from the insider.",
}


class Recorder:
    """A stand-in for the publications table, keyed by id."""

    def __init__(self):
        self.rows = {}
        self.next_id = 1
        self.notified = []

    def queue(self, file, channel, caption=None, link=None, question=None, course=None, status="pending"):
        identifier = self.next_id
        self.next_id += 1
        self.rows[identifier] = {
            "id": identifier, "file": file, "channel": channel, "status": status,
            "caption": caption, "link": link, "attempts": 0, "last_error": None,
            "external_url": "", "notified_at": None, "question": question, "course": course,
        }
        return identifier

    def due(self, max_attempts=3, limit=20):
        return [
            dict(row) for row in self.rows.values()
            if row["status"] == "pending" and row["attempts"] < max_attempts
        ]

    def record(self, publication_id, status, error="", external_url=""):
        row = self.rows[publication_id]
        row["status"] = status
        row["attempts"] += 1
        row["last_error"] = error or None
        if external_url:
            row["external_url"] = external_url

    def alerts(self):
        return [
            dict(row) for row in self.rows.values()
            if row["status"] in ("needs_token", "failed") and row["notified_at"] is None
        ]

    def mark_notified(self, identifiers):
        for identifier in identifiers:
            self.rows[identifier]["notified_at"] = "now"
        self.notified.append(list(identifiers))


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def wired(monkeypatch, tmp_path, recorder):
    monkeypatch.setattr(admin_app, "VIDEO_DIR", tmp_path / "videos")
    monkeypatch.setattr(db, "video_publications_available", lambda: True)
    monkeypatch.setattr(db, "queue_video_publication", recorder.queue)
    monkeypatch.setattr(db, "get_due_video_publications", recorder.due)
    monkeypatch.setattr(db, "record_video_attempt", recorder.record)
    monkeypatch.setattr(db, "get_unnotified_video_alerts", recorder.alerts)
    monkeypatch.setattr(db, "mark_video_alerts_notified", recorder.mark_notified)
    monkeypatch.setattr(db, "get_video_publication", lambda pid: dict(recorder.rows[pid]))
    monkeypatch.setattr(db, "get_video_question", lambda cert=None: dict(QUESTION))
    monkeypatch.setattr(admin_app, "notify_admin", lambda subject, body: True)

    # ffmpeg is a system binary the test host need not have: the assembly is
    # covered in test_quizvideo, this suite is about what happens afterwards.
    def fake_build(question, output, cta_url="", countdown=3, runner=None):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"\x00")
        storyboard = quizvideo.build_storyboard(question, cta_url=cta_url, countdown=countdown)
        return {"path": output, "frames": [], "storyboard": storyboard, "duration": 18.0}

    monkeypatch.setattr(admin_app, "build_quiz_video", fake_build)

    (tmp_path / "videos").mkdir(parents=True, exist_ok=True)

    return recorder


def queue_clip(wired, channel="linkedin", status="pending"):
    name = f"quiz-7-42-{channel}.mp4"
    (admin_app.VIDEO_DIR / name).write_bytes(b"\x00")
    return wired.queue(name, channel, caption="caption", status=status)


def publishers(monkeypatch, result):
    monkeypatch.setattr(videopub, "default_publishers", lambda: {"linkedin": result, "x": result})


def test_a_queued_clip_is_published(wired, monkeypatch):
    identifier = queue_clip(wired)
    publishers(monkeypatch, lambda clip, caption: "https://linkedin.com/p/1")

    counters = admin_app.dispatch_pending_videos()

    assert counters["published"] == 1
    assert wired.rows[identifier]["status"] == "published"
    assert wired.rows[identifier]["external_url"] == "https://linkedin.com/p/1"


def test_an_expired_token_stops_the_retries_immediately(wired, monkeypatch):
    identifier = queue_clip(wired)

    def expired(clip, caption):
        raise videopub.PublicationError("token expired", status_code=401)

    publishers(monkeypatch, expired)

    counters = admin_app.dispatch_pending_videos()

    assert counters["needs_token"] == 1
    assert wired.rows[identifier]["status"] == "needs_token"
    assert wired.rows[identifier]["attempts"] == 1


def test_a_transient_failure_stays_in_the_queue(wired, monkeypatch):
    identifier = queue_clip(wired)

    def unavailable(clip, caption):
        raise videopub.PublicationError("Service Unavailable", status_code=503)

    publishers(monkeypatch, unavailable)

    counters = admin_app.dispatch_pending_videos()

    assert counters["retry"] == 1
    assert wired.rows[identifier]["status"] == "pending"
    assert wired.rows[identifier]["attempts"] == 1


def test_a_transient_failure_gives_up_after_the_last_attempt(wired, monkeypatch):
    identifier = queue_clip(wired)

    def unavailable(clip, caption):
        raise videopub.PublicationError("Service Unavailable", status_code=503)

    publishers(monkeypatch, unavailable)

    for _ in range(admin_app.VIDEO_MAX_ATTEMPTS):
        admin_app.dispatch_pending_videos()

    assert wired.rows[identifier]["status"] == "failed"
    assert wired.rows[identifier]["attempts"] == admin_app.VIDEO_MAX_ATTEMPTS


def test_the_admin_is_told_once_per_publication(wired, monkeypatch):
    queue_clip(wired)
    sent = []
    monkeypatch.setattr(admin_app, "notify_admin", lambda subject, body: sent.append(subject) or True)

    def expired(clip, caption):
        raise videopub.PublicationError("token expired", status_code=401)

    publishers(monkeypatch, expired)

    admin_app.dispatch_pending_videos()
    admin_app.dispatch_pending_videos()

    assert len(sent) == 1


def test_an_unsendable_notification_keeps_the_alert_pending(wired, monkeypatch):
    queue_clip(wired)
    monkeypatch.setattr(admin_app, "notify_admin", lambda subject, body: False)

    def expired(clip, caption):
        raise videopub.PublicationError("token expired", status_code=401)

    publishers(monkeypatch, expired)
    admin_app.dispatch_pending_videos()

    assert wired.notified == []
    assert len(wired.alerts()) == 1


def test_nothing_runs_before_the_table_is_deployed(wired, monkeypatch):
    monkeypatch.setattr(db, "video_publications_available", lambda: False)

    def explode(*args, **kwargs):
        raise AssertionError("the queue must not be read without the table")

    monkeypatch.setattr(db, "get_due_video_publications", explode)

    assert admin_app.dispatch_pending_videos() == {
        "published": 0, "retry": 0, "needs_token": 0, "failed": 0, "manual": 0
    }


def test_a_planned_entry_builds_one_clip_per_channel(wired, monkeypatch):
    publishers(monkeypatch, lambda clip, caption: "https://linkedin.com/p/1")

    result = admin_app.run_scheduled_video_publication(
        certification_id=7, landing="https://examboot.net/guide/cissp", content="99"
    )

    assert len(result["files"]) == len(videopub.CHANNELS)
    assert result["publication"]["published"] == len(videopub.AUTOMATIC_CHANNELS)
    assert result["publication"]["manual"] == len(videopub.MANUAL_CHANNELS)


def test_each_channel_gets_its_own_tagged_link(wired, monkeypatch):
    publishers(monkeypatch, lambda clip, caption: "")

    admin_app.run_scheduled_video_publication(
        certification_id=7, landing="https://examboot.net/guide/cissp", content="99"
    )

    links = {row["channel"]: row["link"] for row in wired.rows.values()}
    assert "utm_source=linkedin" in links["linkedin"]
    assert "utm_source=tiktok" in links["tiktok"]
    assert all("utm_content=99" in link for link in links.values())


def test_manual_channels_are_queued_but_never_attempted(wired, monkeypatch):
    def only_automatic(clip, caption):
        return "https://linkedin.com/p/1"

    publishers(monkeypatch, only_automatic)

    admin_app.run_scheduled_video_publication(certification_id=7, landing="https://examboot.net")

    manual = [row for row in wired.rows.values() if row["channel"] in videopub.MANUAL_CHANNELS]
    assert manual
    assert all(row["status"] == "manual" and row["attempts"] == 0 for row in manual)


def test_a_certification_without_a_usable_question_fails_the_entry(wired, monkeypatch):
    monkeypatch.setattr(db, "get_video_question", lambda cert=None: None)

    with pytest.raises(ValueError):
        admin_app.run_scheduled_video_publication(certification_id=7, landing="https://examboot.net")


def test_an_expired_token_does_not_lose_the_other_channels(wired, monkeypatch):
    def expired(clip, caption):
        raise videopub.PublicationError("token expired", status_code=401)

    publishers(monkeypatch, expired)

    result = admin_app.run_scheduled_video_publication(certification_id=7, landing="https://examboot.net")

    assert len(result["files"]) == len(videopub.CHANNELS)
    assert result["publication"]["needs_token"] == len(videopub.AUTOMATIC_CHANNELS)
