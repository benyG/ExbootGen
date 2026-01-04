import random
from collections import Counter
from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app import _generate_auto_schedule  # noqa: E402


def _sample_certifications():
    return [
        {"id": 1, "name": "Cert A", "provider_id": 10, "provider_name": "Provider A"},
        {"id": 2, "name": "Cert B", "provider_id": 11, "provider_name": "Provider B"},
        {"id": 3, "name": "Cert C", "provider_id": 12, "provider_name": "Provider C"},
    ]


def test_generate_auto_schedule_respects_quotas_and_cadence():
    entries = _generate_auto_schedule(
        _sample_certifications(),
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

    # Automatic plan enforces image flag and leaves link blank for generation time.
    assert all(entry.get("addImage") for entry in entries)
    assert all(not entry.get("link") for entry in entries)
