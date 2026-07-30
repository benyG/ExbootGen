import os
import sys
from datetime import date
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attribution import default_campaign, tag_link  # noqa: E402


def _params(url):
    return {key: value[0] for key, value in parse_qs(urlparse(url).query).items()}


def test_tags_a_link_with_channel_and_campaign():
    url = tag_link(
        "https://app.examboot.net/shared/Test_ABC",
        channel="linkedin",
        campaign="CISSP July",
        content="42",
    )

    assert _params(url) == {
        "utm_source": "linkedin",
        "utm_medium": "social",
        "utm_campaign": "cissp-july",
        "utm_content": "42",
    }


def test_maps_each_channel_to_its_medium():
    assert _params(tag_link("https://e.net/x", channel="article"))["utm_medium"] == "blog"
    assert _params(tag_link("https://e.net/x", channel="x"))["utm_medium"] == "social"
    assert _params(tag_link("https://e.net/x", channel="outreach"))["utm_medium"] == "outreach"


def test_preserves_existing_query_parameters():
    url = tag_link(
        "https://app.examboot.net/filament/exams/assess?ex=Test_ABC",
        channel="x",
        campaign="quiz",
    )

    assert _params(url)["ex"] == "Test_ABC"
    assert _params(url)["utm_source"] == "x"


def test_never_overwrites_tags_already_on_the_url():
    url = tag_link(
        "https://e.net/x?utm_source=newsletter",
        channel="linkedin",
        campaign="july",
    )

    assert _params(url)["utm_source"] == "newsletter"


def test_leaves_an_unknown_channel_untouched():
    original = "https://e.net/x"

    assert tag_link(original, channel="carrier-pigeon") == original


def test_leaves_a_blank_or_relative_url_untouched():
    assert tag_link("", channel="x") == ""
    assert tag_link("   ", channel="x") == "   "
    assert tag_link("/shared/Test", channel="x") == "/shared/Test"


def test_omits_content_when_none_is_supplied():
    assert "utm_content" not in _params(tag_link("https://e.net/x", channel="x"))


def test_default_campaign_combines_subject_and_month():
    assert default_campaign("Exam tips", on=date(2026, 7, 15)) == "exam-tips-2026-07"


def test_default_campaign_falls_back_to_the_month_alone():
    assert default_campaign("", on=date(2026, 7, 15)) == "2026-07"


def test_campaign_is_slugified_for_safe_grouping():
    url = tag_link("https://e.net/x", channel="x", campaign="  CISSP / Domaine 1  ")

    assert _params(url)["utm_campaign"] == "cissp-domaine-1"
