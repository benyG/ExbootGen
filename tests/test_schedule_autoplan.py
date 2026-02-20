import random
from collections import Counter
from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import _autoplan_subject_rule, _generate_auto_schedule  # noqa: E402


def _sample_certifications():
    return [
        {"id": 1, "name": "Cert A", "provider_id": 10, "provider_name": "Provider A"},
        {"id": 2, "name": "Cert B", "provider_id": 11, "provider_name": "Provider B"},
        {"id": 3, "name": "Cert C", "provider_id": 12, "provider_name": "Provider C"},
    ]


def test_generate_auto_schedule_respects_quotas_and_cadence():
    entries = _generate_auto_schedule(
        _sample_certifications(),
        carousel_topics=[],
        today=date(2024, 1, 15),
        rng=random.Random(123),
    )

    social_entries = [e for e in entries if set(e.get("channels", [])) == {"linkedin", "x"}]
    article_entries = [e for e in entries if e.get("contentType") == "article_long"]

    assert len(social_entries) == 31  # January 2024

    # Every day has a LinkedIn + X slot.
    per_day = {}
    for entry in social_entries:
        per_day.setdefault(entry["day"], []).append(entry)
    assert len(per_day) == 31
    assert all(day_entries for day_entries in per_day.values())

    # Article cadence is at least every two days.
    article_days = sorted({e["day"] for e in article_entries})
    day_numbers = [int(day.split("-")[2]) for day in article_days]
    assert all((curr - prev) <= 2 for prev, curr in zip(day_numbers, day_numbers[1:]))

    # Subject quotas: max testimonies and engagement, minima for the three anchors.
    subject_counts = Counter(entry["subject"] for entry in social_entries)
    assert subject_counts["experience_testimony"] <= 2
    assert subject_counts["engagement_community"] <= 3
    for key in ("certification_presentation", "preparation_methodology", "career_impact"):
        assert subject_counts[key] >= 1

    # Automatic plan keeps social/article links deferred until runtime.
    assert all(not entry.get("link") for entry in social_entries + article_entries)

    # Subject policy controls auto image insertion for LinkedIn/X publications.
    for entry in social_entries:
        if entry["subject"] in {"certification_presentation", "engagement_community"}:
            assert entry.get("addImage") is False
        else:
            assert entry.get("addImage") is True


def test_generate_auto_schedule_adds_one_carousel_per_week_when_topics_available():
    carousel_topics = [
        {"id": idx, "topic": f"Sujet {idx}", "question_to_address": f"Question {idx}"}
        for idx in range(1, 8)
    ]

    entries = _generate_auto_schedule(
        _sample_certifications(),
        carousel_topics=carousel_topics,
        today=date(2024, 1, 15),
        rng=random.Random(321),
    )

    carousel_entries = [entry for entry in entries if entry.get("channels") == ["carousel"]]
    week_keys = {
        (date.fromisoformat(entry["day"]).isocalendar().year, date.fromisoformat(entry["day"]).isocalendar().week)
        for entry in carousel_entries
    }

    assert len(carousel_entries) == len(week_keys)
    assert len(carousel_entries) == 5  # January 2024 spans five ISO weeks.
    assert all(entry.get("contentType") == "carousel_pdf" for entry in carousel_entries)
    assert all(entry.get("carouselTopicId") for entry in carousel_entries)
    assert all(entry.get("addImage") is False for entry in carousel_entries)
    assert all(entry.get("link") == "https://examboot.net" for entry in carousel_entries)


def test_generate_auto_schedule_skips_carousel_when_no_topics_available():
    entries = _generate_auto_schedule(
        _sample_certifications(),
        carousel_topics=[],
        today=date(2024, 1, 15),
        rng=random.Random(123),
    )

    assert not [entry for entry in entries if entry.get("channels") == ["carousel"]]


def test_autoplan_subject_rules_match_expected_table():
    assert _autoplan_subject_rule("certification_presentation") == {"add_image": False, "link_mode": "slug"}
    assert _autoplan_subject_rule("preparation_methodology") == {"add_image": True, "link_mode": "generated_test"}
    assert _autoplan_subject_rule("career_impact") == {"add_image": True, "link_mode": "slug"}
    assert _autoplan_subject_rule("experience_testimony") == {"add_image": True, "link_mode": "generated_test"}
    assert _autoplan_subject_rule("engagement_community") == {"add_image": False, "link_mode": "generated_test"}
