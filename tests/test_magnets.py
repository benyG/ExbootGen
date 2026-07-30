import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import magnets  # noqa: E402


PDF_BYTES = b"%PDF-1.4 pretend this is a carousel"

PAGES = [
    {"headline": "The obvious answer is the wrong one", "subtext": "", "key_message": ""},
    {"headline": "What the examiner is really testing", "subtext": "", "key_message": ""},
    {"headline": "   ", "subtext": "", "key_message": ""},
]


class FakeResponse:
    def __init__(self, status_code=201, payload=None, invalid=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._invalid = invalid

    def json(self):
        if self._invalid:
            raise ValueError("not json")
        return self._payload


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "carousel.pdf"
    path.write_bytes(PDF_BYTES)
    return path


@pytest.fixture
def posted(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse(payload={"slug": "cissp-traps", "url": "https://examboot.net/guide/cissp-traps"})

    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")
    monkeypatch.setattr(magnets.requests, "post", fake_post)
    return captured


def test_the_asset_is_sent_with_its_pdf_encoded(pdf, posted):
    result = magnets.push_magnet(
        "CISSP domain 3 traps",
        "Ten questions candidates get wrong.",
        bullets="One\nTwo",
        course=5,
        pdf_path=pdf,
    )

    assert result["url"] == "https://examboot.net/guide/cissp-traps"
    assert base64.b64decode(posted["json"]["file"]) == PDF_BYTES
    assert posted["json"]["course"] == 5
    assert posted["json"]["bullets"] == "One\nTwo"


def test_the_api_key_travels_with_the_asset(pdf, posted):
    magnets.push_magnet("Title", "Promise", pdf_path=pdf)

    assert posted["headers"]["x-api-key"] == "test-key"


def test_an_asset_without_a_file_is_still_publishable(posted):
    magnets.push_magnet("Title", "Promise")

    assert "file" not in posted["json"]


def test_a_missing_api_key_is_refused_before_the_network(monkeypatch, pdf):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "")

    def explode(*args, **kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr(magnets.requests, "post", explode)

    with pytest.raises(magnets.MagnetPublicationError):
        magnets.push_magnet("Title", "Promise", pdf_path=pdf)


def test_an_asset_without_a_promise_is_refused(monkeypatch):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")

    with pytest.raises(magnets.MagnetPublicationError):
        magnets.push_magnet("Title", "   ")


def test_a_refusal_from_the_platform_is_surfaced(monkeypatch, pdf):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")
    monkeypatch.setattr(magnets.requests, "post", lambda *a, **k: FakeResponse(status_code=422))

    with pytest.raises(magnets.MagnetPublicationError):
        magnets.push_magnet("Title", "Promise", pdf_path=pdf)


def test_a_response_without_a_url_is_refused(monkeypatch):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")
    monkeypatch.setattr(magnets.requests, "post", lambda *a, **k: FakeResponse(payload={"slug": "x"}))

    with pytest.raises(magnets.MagnetPublicationError):
        magnets.push_magnet("Title", "Promise")


def test_a_file_that_is_not_a_pdf_never_leaves_the_console(monkeypatch, tmp_path):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")

    def explode(*args, **kwargs):
        raise AssertionError("no request may be made with a non-PDF file")

    monkeypatch.setattr(magnets.requests, "post", explode)

    fake = tmp_path / "not.pdf"
    fake.write_bytes(b"<html>gotcha</html>")

    with pytest.raises(magnets.MagnetPublicationError):
        magnets.push_magnet("Title", "Promise", pdf_path=fake)


def test_a_missing_file_is_reported_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(magnets, "EXAMBOOT_API_KEY", "test-key")

    with pytest.raises(magnets.MagnetPublicationError, match="introuvable"):
        magnets.push_magnet("Title", "Promise", pdf_path=tmp_path / "absent.pdf")


def test_the_landing_link_carries_the_campaign():
    link = magnets.magnet_link(
        "https://examboot.net/guide/cissp-traps", channel="linkedin", content="42"
    )

    assert "utm_source=linkedin" in link
    assert "utm_campaign=lead-magnet" in link
    assert "utm_content=42" in link


def test_the_promise_joins_the_question_and_the_subject():
    promise = magnets.build_promise("CISSP domain 3", "Why do candidates fail cryptography?")

    assert promise.startswith("Why do candidates fail cryptography?")
    assert "CISSP domain 3" in promise


def test_a_topic_without_a_question_still_promises_something():
    assert magnets.build_promise("CISSP domain 3", "") == "Five slides on CISSP domain 3, and the reasoning behind each one."


def test_the_bullets_are_the_carousel_headlines():
    bullets = magnets.bullets_from_pages(PAGES)

    assert bullets.splitlines() == [
        "The obvious answer is the wrong one",
        "What the examiner is really testing",
    ]


def test_bullets_survive_a_malformed_payload():
    assert magnets.bullets_from_pages([None, "text", {}]) == ""
