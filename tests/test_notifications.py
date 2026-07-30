import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import notifications  # noqa: E402


EXPIRED = {
    "id": 1,
    "file": "quiz-7-42-linkedin.mp4",
    "channel": "linkedin",
    "status": "needs_token",
    "last_error": "Erreur lors de la publication LinkedIn (401): token expired",
}

FAILED = {
    "id": 2,
    "file": "quiz-7-42-x.mp4",
    "channel": "x",
    "status": "failed",
    "last_error": "Erreur X (503): Service Unavailable",
}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(notifications, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifications, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(notifications, "SMTP_FROM", "console@example.com")


def test_nothing_is_sent_without_smtp(monkeypatch):
    monkeypatch.setattr(notifications, "SMTP_HOST", "")

    def explode(message):
        raise AssertionError("no email may be sent without SMTP configured")

    assert notifications.notify_admin("subject", "body", sender=explode) is False


def test_nothing_is_sent_without_a_recipient(monkeypatch):
    monkeypatch.setattr(notifications, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(notifications, "ADMIN_EMAIL", "")

    assert notifications.notify_admin("subject", "body", sender=lambda m: None) is False


def test_the_email_carries_the_subject_and_the_body(configured):
    sent = {}

    def sender(message):
        sent["subject"] = message["Subject"]
        sent["to"] = message["To"]
        sent["body"] = message.get_content()

    assert notifications.notify_admin("Jeton expiré", "Reconnectez LinkedIn.", sender=sender) is True
    assert sent["subject"] == "Jeton expiré"
    assert sent["to"] == "admin@example.com"
    assert "Reconnectez LinkedIn." in sent["body"]


def test_an_smtp_failure_never_propagates(configured):
    def sender(message):
        raise OSError("connection refused")

    assert notifications.notify_admin("subject", "body", sender=sender) is False


def test_an_expired_token_leads_the_subject():
    subject, _ = notifications.build_failure_notice([EXPIRED, FAILED])

    assert "reconnectez" in subject.lower()
    assert "linkedin" in subject.lower()


def test_the_subject_falls_back_when_only_publications_failed():
    subject, _ = notifications.build_failure_notice([FAILED])

    assert "reconnectez" not in subject.lower()
    assert "1" in subject


def test_the_body_separates_expired_tokens_from_plain_failures():
    _, body = notifications.build_failure_notice([EXPIRED, FAILED])

    assert "quiz-7-42-linkedin.mp4" in body
    assert "quiz-7-42-x.mp4" in body
    assert "jeton a expiré" in body


def test_the_body_says_the_clips_can_be_posted_by_hand():
    _, body = notifications.build_failure_notice([EXPIRED])

    assert "à la main" in body


def test_the_body_links_to_the_console_when_it_has_an_address():
    _, body = notifications.build_failure_notice([EXPIRED], console_url="https://console.example.com/videos")

    assert "https://console.example.com/videos" in body


def test_a_publication_without_an_error_still_reads():
    _, body = notifications.build_failure_notice([{**EXPIRED, "last_error": None}])

    assert "raison inconnue" in body
