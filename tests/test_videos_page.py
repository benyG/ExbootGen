import os
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as admin_app  # noqa: E402
import db  # noqa: E402
import quizvideo  # noqa: E402


QUESTION = {
    "id": 42,
    "cert_id": 7,
    "cert_name": "CISSP",
    "text": "Which control best mitigates a privileged insider exfiltrating data?",
    "options": [
        {"text": "Full-disk encryption", "isok": False},
        {"text": "Separation of duties", "isok": True},
    ],
    "note": "Encryption protects the data in transit, not from the insider allowed to read it.",
}

MAGNETS = [
    {"id": 1, "slug": "cissp-traps", "title": "The 10 traps of CISSP domain 3", "pub": 1},
    {"id": 2, "slug": "draft", "title": "Not online yet", "pub": 0},
]


QUEUE = [
    {
        "id": 1, "file": "quiz-7-42-linkedin.mp4", "channel": "linkedin",
        "status": "needs_token", "caption": "caption", "link": "https://examboot.net",
        "attempts": 1, "last_error": "Erreur LinkedIn (401): token expired",
        "external_url": "", "notified_at": None, "published_at": None,
        "created_at": None, "question": 42, "course": 7, "course_name": "CISSP",
    },
    {
        "id": 2, "file": "quiz-7-42-tiktok.mp4", "channel": "tiktok",
        "status": "manual", "caption": "caption", "link": "https://examboot.net",
        "attempts": 0, "last_error": None, "external_url": "", "notified_at": None,
        "published_at": None, "created_at": None, "question": 42, "course": 7,
        "course_name": "CISSP",
    },
]

SUMMARY = {"published": 3, "pending": 1, "manual": 2, "needs_token": 1, "failed": 0}

@pytest.fixture
def calls():
    return {}


@pytest.fixture
def client(monkeypatch, tmp_path, calls):
    monkeypatch.setattr(admin_app, "VIDEO_DIR", tmp_path / "videos")
    monkeypatch.setattr(db, "get_public_certifications", lambda: [{"id": 7, "name": "CISSP", "provider_name": "ISC2"}])
    monkeypatch.setattr(db, "magnets_available", lambda: True)
    monkeypatch.setattr(db, "get_magnets", lambda: [dict(row) for row in MAGNETS])
    monkeypatch.setattr(db, "get_video_question", lambda cert=None: dict(QUESTION))

    def fake_build(question, output, cta_url="", countdown=3, runner=None):
        calls["question"] = question
        calls["output"] = output
        calls["cta"] = cta_url
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fake mp4")
        storyboard = quizvideo.build_storyboard(question, cta_url=cta_url, countdown=countdown)
        return {"path": output, "frames": [], "storyboard": storyboard, "duration": 18.0}

    monkeypatch.setattr(admin_app, "build_quiz_video", fake_build)
    monkeypatch.setattr(db, "video_publications_available", lambda: True)
    monkeypatch.setattr(db, "get_video_publications", lambda status=None, limit=100: [dict(row) for row in QUEUE])
    monkeypatch.setattr(db, "get_video_publication_summary", lambda: dict(SUMMARY))
    monkeypatch.setattr(db, "queue_video_publication", lambda **kwargs: calls.setdefault("queued", []).append(kwargs) or 1)
    monkeypatch.setattr(admin_app, "dispatch_pending_videos", lambda *a, **k: calls.setdefault("dispatched", 0) or calls.update({"dispatched": 1}))

    admin_app.app.config["TESTING"] = True
    client = admin_app.app.test_client()

    with client.session_transaction() as sess:
        sess["user"] = "exboot"
        sess["last_activity"] = time.time()

    return client


def test_the_factory_offers_the_catalogue(client):
    body = client.get("/videos").get_data(as_text=True)

    assert "CISSP" in body
    assert "Vidéos quiz" in body


def test_only_published_assets_are_offered_as_a_destination(client):
    body = client.get("/videos").get_data(as_text=True)

    assert "The 10 traps of CISSP domain 3" in body
    assert "Not online yet" not in body


def test_a_clip_is_built_from_a_question_of_the_catalogue(client, calls):
    response = client.post("/videos/generate", data={"certification_id": "7", "channels": ["tiktok"]})

    assert response.status_code == 302
    assert calls["question"]["id"] == 42
    assert calls["output"].name == "quiz-7-42-tiktok.mp4"


def test_the_closing_link_points_at_the_chosen_asset(client, calls):
    client.post("/videos/generate", data={"magnet": "cissp-traps", "channels": ["shorts"]})

    assert "/guide/cissp-traps" in calls["cta"]
    assert "utm_source=shorts" in calls["cta"]
    assert "utm_content=42" in calls["cta"]


def test_without_an_asset_the_link_still_reaches_the_platform(client, calls):
    client.post("/videos/generate", data={"channels": ["shorts"]})

    assert calls["cta"].startswith(admin_app.EXAMBOOT_BASE_URL)
    assert "utm_source=shorts" in calls["cta"]


def test_a_catalogue_without_a_usable_question_says_so(client, monkeypatch):
    monkeypatch.setattr(db, "get_video_question", lambda cert=None: None)

    response = client.post("/videos/generate", data={"certification_id": "7"})

    assert response.status_code == 302
    assert "error=" in response.headers["Location"]


def test_a_refused_question_never_produces_a_broken_clip(client, monkeypatch):
    def explode(question, output, cta_url="", countdown=3, runner=None):
        raise quizvideo.QuizVideoError("Cette question est trop longue pour une vidéo verticale.")

    monkeypatch.setattr(admin_app, "build_quiz_video", explode)

    response = client.post("/videos/generate", data={})

    assert response.status_code == 302
    assert "error=" in response.headers["Location"]


def test_the_storyboard_of_the_last_clip_is_shown_once(client):
    client.post("/videos/generate", data={})

    first = client.get("/videos").get_data(as_text=True)
    second = client.get("/videos").get_data(as_text=True)

    assert "image par image" in first
    assert "image par image" not in second


def test_a_produced_clip_can_be_downloaded(client):
    client.post("/videos/generate", data={})

    response = client.get("/videos/quiz-7-42-shorts.mp4")

    assert response.status_code == 200
    assert response.data == b"fake mp4"


def test_a_path_outside_the_folder_is_refused(client):
    client.post("/videos/generate", data={})

    response = client.get("/videos/..%2f..%2fapp.py")

    assert response.status_code in (400, 404)


def test_only_video_files_are_served(client, tmp_path):
    client.post("/videos/generate", data={})
    (admin_app.VIDEO_DIR / "secret.env").write_text("API_KEY=xxx")

    response = client.get("/videos/secret.env")

    assert response.status_code == 404


def test_one_clip_is_built_per_selected_channel(client, calls):
    client.post("/videos/generate", data={"channels": ["linkedin", "tiktok"]})

    queued = {row["channel"]: row for row in calls["queued"]}
    assert set(queued) == {"linkedin", "tiktok"}
    assert queued["linkedin"]["file"] == "quiz-7-42-linkedin.mp4"
    assert queued["tiktok"]["file"] == "quiz-7-42-tiktok.mp4"


def test_an_automatic_channel_is_queued_and_a_manual_one_is_not(client, calls):
    client.post("/videos/generate", data={"channels": ["linkedin", "tiktok"]})

    queued = {row["channel"]: row["status"] for row in calls["queued"]}
    assert queued["linkedin"] == "pending"
    assert queued["tiktok"] == "manual"


def test_the_queue_is_dispatched_as_soon_as_a_clip_is_generated(client, calls):
    client.post("/videos/generate", data={"channels": ["linkedin"]})

    assert calls["dispatched"] == 1


def test_the_caption_carries_the_tagged_link(client, calls):
    client.post("/videos/generate", data={"channels": ["linkedin"]})

    caption = calls["queued"][0]["caption"]
    assert "utm_source=linkedin" in caption
    assert "CISSP" in caption


def test_a_publication_waiting_for_a_token_is_surfaced(client):
    body = client.get("/videos").get_data(as_text=True)

    assert "En attente d'une action de votre part" in body
    assert "token expired" in body
    assert "quiz-7-42-linkedin.mp4" in body


def test_the_queue_shows_what_each_status_holds(client):
    body = client.get("/videos").get_data(as_text=True)

    assert "File de publication" in body
    assert "À publier à la main" in body


def test_the_page_survives_a_platform_without_the_queue(client, monkeypatch):
    monkeypatch.setattr(db, "video_publications_available", lambda: False)

    def explode(status=None, limit=100):
        raise AssertionError("the queue must not be read without the table")

    monkeypatch.setattr(db, "get_video_publications", explode)

    body = client.get("/videos").get_data(as_text=True)

    assert "File de publication pas encore déployée" in body


def test_retrying_clears_the_attempts_and_publishes_again(client, calls, monkeypatch):
    requeued = {}
    monkeypatch.setattr(db, "requeue_video_publication", lambda pid: requeued.update({"id": pid}))
    monkeypatch.setattr(db, "get_video_publication", lambda pid: {"id": pid, "status": "published"})

    response = client.post("/videos/queue/1/retry")

    assert requeued["id"] == 1
    assert calls["dispatched"] == 1
    assert "ok=" in response.headers["Location"]


def test_a_retry_that_hits_the_same_expired_token_says_so(client, monkeypatch):
    monkeypatch.setattr(db, "requeue_video_publication", lambda pid: None)
    monkeypatch.setattr(db, "get_video_publication", lambda pid: {"id": pid, "status": "needs_token"})

    response = client.post("/videos/queue/1/retry")

    assert "error=" in response.headers["Location"]
    assert "reconnectez" in response.headers["Location"].lower()


def test_a_clip_posted_by_hand_can_be_recorded(client, monkeypatch):
    marked = {}
    monkeypatch.setattr(db, "mark_video_published", lambda pid, url="": marked.update({"id": pid, "url": url}))

    response = client.post("/videos/queue/2/published", data={"url": "https://tiktok.com/@x/video/1"})

    assert marked == {"id": 2, "url": "https://tiktok.com/@x/video/1"}
    assert response.status_code == 302
