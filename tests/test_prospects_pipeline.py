import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as admin_app  # noqa: E402
import db  # noqa: E402


PROSPECTS = [
    {
        "id": 1,
        "name": "Marie Dupont",
        "email": "marie@cyberformation.fr",
        "organisation": "CyberFormation",
        "profile_url": None,
        "source": "linkedin",
        "status": "new",
        "gift_url": None,
        "gift_sent_at": None,
        "last_contact_at": None,
        "follow_ups": 0,
        "notes": None,
        "course": 5,
        "user": None,
        "course_name": "CISSP",
    },
    {
        "id": 2,
        "name": "Paul Martin",
        "email": None,
        "organisation": None,
        "profile_url": None,
        "source": "manual",
        "status": "gift_sent",
        "gift_url": "https://examboot.net/t/xyz?utm_source=outreach",
        "gift_sent_at": None,
        "last_contact_at": None,
        "follow_ups": 1,
        "notes": None,
        "course": None,
        "user": None,
        "course_name": None,
    },
]

FUNNEL = {
    "new": 1,
    "gift_sent": 1,
    "replied": 0,
    "signed_up": 0,
    "customer": 0,
    "declined": 0,
}


@pytest.fixture
def calls():
    return {}


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setattr(db, "prospects_available", lambda: True)
    monkeypatch.setattr(db, "get_prospects", lambda status=None: [dict(row) for row in PROSPECTS])
    monkeypatch.setattr(db, "get_prospect", lambda pid: next((dict(r) for r in PROSPECTS if r["id"] == pid), None))
    monkeypatch.setattr(db, "get_prospect_funnel", lambda: dict(FUNNEL))
    monkeypatch.setattr(
        db,
        "get_public_certifications",
        lambda: [{"id": 5, "name": "CISSP", "provider_id": 1, "provider_name": "ISC2"}],
    )
    def remember_insert(**kwargs):
        calls["insert"] = kwargs
        return 1

    monkeypatch.setattr(db, "insert_prospect", remember_insert)
    monkeypatch.setattr(db, "record_prospect_gift", lambda pid, url: calls.update({"gift": (pid, url)}))
    monkeypatch.setattr(db, "record_prospect_follow_up", lambda pid: calls.update({"follow_up": pid}))
    monkeypatch.setattr(db, "update_prospect_status", lambda pid, status: calls.update({"status": (pid, status)}))
    monkeypatch.setattr(db, "match_prospects_to_accounts", lambda: 3)

    admin_app.app.config["TESTING"] = True
    client = admin_app.app.test_client()

    with client.session_transaction() as sess:
        sess["user"] = "exboot"
        sess["last_activity"] = time.time()

    return client


def test_the_pipeline_lists_every_prospect(client):
    body = client.get("/prospects").get_data(as_text=True)

    assert "Marie Dupont" in body
    assert "Paul Martin" in body
    assert "CISSP" in body


def test_the_funnel_counts_are_rendered(client):
    body = client.get("/prospects").get_data(as_text=True)

    assert "Sourcé" in body
    assert "Cadeau envoyé" in body


def test_an_unknown_status_filter_falls_back_to_the_whole_list(client, monkeypatch):
    seen = {}

    def capture(status=None):
        seen["status"] = status
        return []

    monkeypatch.setattr(db, "get_prospects", capture)

    client.get("/prospects?status=nonsense")

    assert seen["status"] is None


def test_a_known_status_filter_reaches_the_query(client, monkeypatch):
    seen = {}

    def capture(status=None):
        seen["status"] = status
        return []

    monkeypatch.setattr(db, "get_prospects", capture)

    client.get("/prospects?status=customer")

    assert seen["status"] == "customer"


def test_selecting_a_prospect_shows_the_three_messages(client):
    body = client.get("/prospects?prospect=1").get_data(as_text=True)

    assert "La séquence pour Marie Dupont" in body
    assert "Le cadeau" in body
    assert "La sortie" in body


def test_the_page_survives_a_platform_without_the_table(client, monkeypatch):
    monkeypatch.setattr(db, "prospects_available", lambda: False)

    def explode(status=None):
        raise AssertionError("the pipeline query must not run without the table")

    monkeypatch.setattr(db, "get_prospects", explode)

    response = client.get("/prospects")

    assert response.status_code == 200
    assert "Pipeline pas encore déployé" in response.get_data(as_text=True)


def test_adding_a_prospect_stores_the_form(client, calls):
    response = client.post(
        "/prospects/add",
        data={"name": "Alice Roy", "email": "alice@example.com", "course": "5", "source": "linkedin"},
    )

    assert response.status_code == 302
    assert calls["insert"]["name"] == "Alice Roy"
    assert calls["insert"]["email"] == "alice@example.com"
    assert calls["insert"]["course"] == 5


def test_a_nameless_prospect_is_refused(client, calls):
    client.post("/prospects/add", data={"name": "  ", "email": "alice@example.com"})

    assert "insert" not in calls


def test_the_gift_is_generated_and_tagged_back_to_the_prospect(client, calls, monkeypatch):
    monkeypatch.setattr(admin_app, "ensure_exam_url", lambda cert, url: ("https://examboot.net/t/abc", True))

    response = client.post("/prospects/1/gift")

    prospect_id, tagged = calls["gift"]
    assert response.status_code == 302
    assert prospect_id == 1
    assert "utm_source=outreach" in tagged
    assert "utm_content=1" in tagged


def test_the_gift_uses_the_certification_the_trainer_teaches(client, monkeypatch):
    seen = {}

    def capture(cert, url):
        seen["cert"] = cert
        return "https://examboot.net/t/abc", True

    monkeypatch.setattr(admin_app, "ensure_exam_url", capture)

    client.post("/prospects/1/gift")

    assert seen["cert"] == 5


def test_a_prospect_without_a_certification_gets_a_clear_error(client, calls, monkeypatch):
    def explode(cert, url):
        raise AssertionError("no test may be generated without a certification")

    monkeypatch.setattr(admin_app, "ensure_exam_url", explode)

    response = client.post("/prospects/2/gift")

    assert response.status_code == 302
    assert "certification" in response.headers["Location"]
    assert "gift" not in calls


def test_a_failing_test_generation_never_marks_the_gift_as_sent(client, calls, monkeypatch):
    def explode(cert, url):
        raise admin_app.ExambootTestGenerationError("La clé API Examboot est manquante.")

    monkeypatch.setattr(admin_app, "ensure_exam_url", explode)

    response = client.post("/prospects/1/gift")

    assert response.status_code == 302
    assert "gift" not in calls


def test_logging_a_follow_up_advances_the_sequence(client, calls):
    client.post("/prospects/1/follow-up")

    assert calls["follow_up"] == 1


def test_a_status_change_reaches_the_database(client, calls):
    client.post("/prospects/1/status", data={"status": "replied"})

    assert calls["status"] == (1, "replied")


def test_an_unknown_status_is_rejected_by_the_database_layer(client, monkeypatch):
    def refuse(pid, status):
        raise ValueError("Statut inconnu : nonsense")

    monkeypatch.setattr(db, "update_prospect_status", refuse)

    response = client.post("/prospects/1/status", data={"status": "nonsense"})

    assert response.status_code == 302
    assert "error=" in response.headers["Location"]


def test_syncing_reconciles_the_pipeline_with_the_accounts(client):
    response = client.post("/prospects/sync")

    assert response.status_code == 302
    assert "ok=" in response.headers["Location"]
