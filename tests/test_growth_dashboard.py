import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as admin_app  # noqa: E402
import db  # noqa: E402


TOTALS = {
    "leads": 200,
    "opt_ins": 120,
    "converted_leads": 40,
    "signups": 50,
    "customers": 10,
    "revenue": 480.0,
    "active_recurring": 1250.0,
}

CHANNELS = [
    {
        "source": "linkedin",
        "campaign": "cissp-2026-07",
        "medium": "social",
        "leads": 120,
        "opt_ins": 80,
        "signups": 30,
        "customers": 8,
        "revenue": 400,
    },
    {
        "source": "x",
        "campaign": "quiz-2026-07",
        "medium": "social",
        "leads": 80,
        "opt_ins": 40,
        "signups": 20,
        "customers": 0,
        "revenue": 0,
    },
]

CERTIFICATIONS = [
    {"cert_id": 1, "cert_name": "CISSP", "leads": 150, "avg_score": 62.4, "converted": 30},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(db, "attribution_available", lambda: True)
    monkeypatch.setattr(db, "get_funnel_totals", lambda days: dict(TOTALS))
    monkeypatch.setattr(db, "get_funnel_by_channel", lambda days: [dict(r) for r in CHANNELS])
    monkeypatch.setattr(db, "get_top_certifications_by_leads", lambda days: [dict(r) for r in CERTIFICATIONS])

    admin_app.app.config["TESTING"] = True
    client = admin_app.app.test_client()

    # The whole console sits behind a session login; sign in as the test client
    # rather than weakening the guard.
    with client.session_transaction() as sess:
        sess["user"] = "exboot"
        sess["last_activity"] = time.time()

    return client


def test_growth_page_renders_the_funnel(client):
    response = client.get("/growth")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Croissance" in body
    assert "200" in body  # leads
    assert "1250" in body or "1 250" in body  # active recurring


def test_growth_page_lists_each_channel(client):
    body = client.get("/growth").get_data(as_text=True)

    assert "linkedin" in body
    assert "cissp-2026-07" in body
    assert "quiz-2026-07" in body


def test_growth_page_shows_the_certifications_that_attract(client):
    body = client.get("/growth").get_data(as_text=True)

    assert "CISSP" in body
    assert "62.4" in body


def test_conversion_rates_are_computed_from_the_totals(client, monkeypatch):
    captured = {}

    original = admin_app.render_template

    def capture(template, **context):
        captured.update(context)
        return original(template, **context)

    monkeypatch.setattr(admin_app, "render_template", capture)
    client.get("/growth")

    # 50 signups out of 200 leads, 10 customers out of 50 signups,
    # 1250 of the 5000 goal.
    assert captured["rates"]["lead_to_signup"] == 25.0
    assert captured["rates"]["signup_to_customer"] == 20.0
    assert captured["rates"]["goal"] == 25.0


def test_period_is_restricted_to_known_windows(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(db, "get_funnel_totals", lambda days: captured.setdefault("days", days) and dict(TOTALS) or dict(TOTALS))

    client.get("/growth?days=999")

    assert captured["days"] == 30


def test_period_accepts_a_supported_window(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(db, "get_funnel_by_channel", lambda days: seen.setdefault("days", days) and [] or [])

    client.get("/growth?days=90")

    assert seen["days"] == 90


def test_channels_are_hidden_but_totals_survive_without_attribution(client, monkeypatch):
    monkeypatch.setattr(db, "attribution_available", lambda: False)

    def explode(days):
        raise AssertionError("the channel query must not run without attribution columns")

    monkeypatch.setattr(db, "get_funnel_by_channel", explode)

    body = client.get("/growth").get_data(as_text=True)

    assert "Attribution pas encore déployée" in body
    assert "200" in body  # totals still rendered


def test_a_zero_denominator_never_divides_by_zero(client, monkeypatch):
    monkeypatch.setattr(
        db,
        "get_funnel_totals",
        lambda days: {**TOTALS, "leads": 0, "signups": 0, "customers": 0, "active_recurring": 0},
    )

    response = client.get("/growth")

    assert response.status_code == 200
