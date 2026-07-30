import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import outreach  # noqa: E402


PROSPECT = {
    "id": 42,
    "name": "Marie Dupont",
    "organisation": "CyberFormation",
    "course_name": "CISSP",
    "status": "new",
    "follow_ups": 0,
    "gift_sent_at": None,
    "gift_url": None,
}


def test_the_gift_link_carries_the_prospect_identifier():
    link = outreach.gift_link("https://examboot.net/t/abc", 42)

    assert "utm_source=outreach" in link
    assert "utm_campaign=trainer-prospecting" in link
    assert "utm_content=42" in link


def test_the_gift_link_keeps_the_original_path_and_query():
    link = outreach.gift_link("https://examboot.net/t/abc?lang=fr", 7)

    assert link.startswith("https://examboot.net/t/abc?")
    assert "lang=fr" in link


def test_the_sequence_is_three_messages_personalised():
    messages = outreach.build_messages(PROSPECT, "https://examboot.net/t/abc")

    assert len(messages) == 3
    assert all("Marie" in message["body"] for message in messages)
    assert all("CISSP" in message["subject"] + message["body"] for message in messages)
    assert "https://examboot.net/t/abc" in messages[0]["body"]
    assert "CyberFormation" in messages[0]["body"]


def test_the_second_message_sells_the_seats_and_the_private_space():
    messages = outreach.build_messages(PROSPECT)

    assert "siège" in messages[1]["body"]
    assert "privé" in messages[1]["body"]


def test_a_missing_certification_never_leaves_a_hole_in_the_message():
    messages = outreach.build_messages({"name": "Jean", "follow_ups": 0})

    assert "votre certification" in messages[0]["subject"]
    assert "[lien du test]" in messages[0]["body"]


def test_the_stored_gift_url_is_used_when_none_is_passed():
    messages = outreach.build_messages({**PROSPECT, "gift_url": "https://examboot.net/t/stored"})

    assert "https://examboot.net/t/stored" in messages[0]["body"]


def test_the_gift_is_due_immediately_for_a_fresh_prospect():
    due = outreach.next_follow_up(PROSPECT, today=date(2026, 7, 28))

    assert due["step"] == 1
    assert due["overdue"] is True


def test_the_second_message_waits_three_days():
    prospect = {**PROSPECT, "follow_ups": 1, "gift_sent_at": datetime(2026, 7, 28, 9, 0)}

    assert outreach.next_follow_up(prospect, today=date(2026, 7, 29))["overdue"] is False
    assert outreach.next_follow_up(prospect, today=date(2026, 7, 31))["overdue"] is True
    assert outreach.next_follow_up(prospect, today=date(2026, 7, 31))["step"] == 2


def test_a_string_timestamp_from_the_database_is_understood():
    prospect = {**PROSPECT, "follow_ups": 1, "gift_sent_at": "2026-07-28 09:00:00"}

    assert outreach.next_follow_up(prospect, today=date(2026, 7, 31))["due"] == date(2026, 7, 31)


def test_nobody_is_chased_after_the_third_message():
    prospect = {**PROSPECT, "follow_ups": 3, "gift_sent_at": datetime(2026, 7, 1, 9, 0)}

    assert outreach.next_follow_up(prospect, today=date(2026, 8, 30)) is None


def test_a_prospect_who_answered_is_left_alone():
    for status in ("replied", "signed_up", "customer", "declined"):
        prospect = {**PROSPECT, "status": status, "follow_ups": 1, "gift_sent_at": datetime(2026, 7, 28)}

        assert outreach.next_follow_up(prospect, today=date(2026, 8, 30)) is None
