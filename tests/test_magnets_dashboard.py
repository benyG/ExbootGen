import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as admin_app  # noqa: E402
import db  # noqa: E402


MAGNETS = [
    {
        "id": 1,
        "slug": "cissp-domain-3-traps",
        "title": "The 10 traps of CISSP domain 3",
        "locale": "en",
        "pub": 1,
        "views": 400,
        "captures": 80,
        "file": "magnets/cissp-domain-3-traps.pdf",
        "created_at": None,
        "course_name": "CISSP",
        "leads": 80,
        "converted": 12,
    },
    {
        "id": 2,
        "slug": "aws-saa-shortlist",
        "title": "AWS SAA shortlist",
        "locale": "en",
        "pub": 0,
        "views": 0,
        "captures": 0,
        "file": None,
        "created_at": None,
        "course_name": None,
        "leads": 0,
        "converted": 0,
    },
]

TOTALS = {"magnets": 2, "published": 1, "views": 400, "captures": 80, "converted": 12}


@pytest.fixture
def calls():
    return {}


@pytest.fixture
def client(monkeypatch, calls):
    monkeypatch.setattr(db, "magnets_available", lambda: True)
    monkeypatch.setattr(db, "get_magnets", lambda: [dict(row) for row in MAGNETS])
    monkeypatch.setattr(db, "get_magnet_totals", lambda: dict(TOTALS))
    monkeypatch.setattr(
        db, "set_magnet_published", lambda magnet_id, published: calls.update({"pub": (magnet_id, published)})
    )

    admin_app.app.config["TESTING"] = True
    client = admin_app.app.test_client()

    with client.session_transaction() as sess:
        sess["user"] = "exboot"
        sess["last_activity"] = time.time()

    return client


def test_the_catalogue_lists_every_asset(client):
    body = client.get("/magnets").get_data(as_text=True)

    assert "The 10 traps of CISSP domain 3" in body
    assert "AWS SAA shortlist" in body
    assert "CISSP" in body


def test_the_landing_url_points_at_the_platform(client):
    body = client.get("/magnets").get_data(as_text=True)

    assert "/guide/cissp-domain-3-traps" in body


def test_the_capture_rate_is_computed_from_the_visits(client, monkeypatch):
    captured = {}
    original = admin_app.render_template

    def capture(template, **context):
        captured.update(context)
        return original(template, **context)

    monkeypatch.setattr(admin_app, "render_template", capture)
    client.get("/magnets")

    # 80 captures out of 400 visits.
    assert captured["magnets"][0]["rate"] == 20.0


def test_an_asset_nobody_visited_never_divides_by_zero(client):
    response = client.get("/magnets")

    assert response.status_code == 200
    assert "0.0%" in response.get_data(as_text=True)


def test_an_asset_without_a_file_is_flagged(client):
    body = client.get("/magnets").get_data(as_text=True)

    assert "sans fichier" in body


def test_the_page_survives_a_platform_without_the_table(client, monkeypatch):
    monkeypatch.setattr(db, "magnets_available", lambda: False)

    def explode():
        raise AssertionError("the catalogue query must not run without the table")

    monkeypatch.setattr(db, "get_magnets", explode)

    response = client.get("/magnets")

    assert response.status_code == 200
    assert "Aimants pas encore déployés" in response.get_data(as_text=True)


def test_taking_an_asset_online_reaches_the_database(client, calls):
    response = client.post("/magnets/2/publish", data={"pub": "1"})

    assert response.status_code == 302
    assert calls["pub"] == (2, True)


def test_taking_an_asset_offline_reaches_the_database(client, calls):
    client.post("/magnets/1/publish", data={"pub": "0"})

    assert calls["pub"] == (1, False)
