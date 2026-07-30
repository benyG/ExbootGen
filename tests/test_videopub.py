import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import videopub  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "quiz.mp4"
    path.write_bytes(b"\x00" * 1024)
    return path


def test_an_expired_token_is_never_retried():
    status, retryable = videopub.classify(401, "The token used in the request has expired")

    assert status == videopub.STATUS_NEEDS_TOKEN
    assert retryable is False


def test_a_missing_scope_is_treated_as_a_credential_problem():
    status, retryable = videopub.classify(403, "insufficient permissions for this scope")

    assert status == videopub.STATUS_NEEDS_TOKEN
    assert retryable is False


def test_a_token_message_is_caught_even_without_the_status():
    status, _ = videopub.classify(0, "Erreur : invalid_token")

    assert status == videopub.STATUS_NEEDS_TOKEN


def test_a_rate_limit_is_worth_retrying():
    status, retryable = videopub.classify(429, "Too Many Requests")

    assert status == videopub.STATUS_FAILED
    assert retryable is True


def test_a_server_error_is_worth_retrying():
    assert videopub.classify(503, "Service Unavailable")[1] is True


def test_a_network_failure_is_worth_retrying():
    assert videopub.classify(0, "connection reset")[1] is True


def test_a_rejected_payload_is_not_worth_retrying():
    status, retryable = videopub.classify(400, "Le fichier est trop volumineux")

    assert status == videopub.STATUS_FAILED
    assert retryable is False


def test_a_manual_channel_is_never_published_automatically(clip):
    def explode(*args, **kwargs):
        raise AssertionError("a manual channel must not reach a publisher")

    outcome = videopub.publish("tiktok", clip, "caption", publishers={"tiktok": explode})

    assert outcome["status"] == videopub.STATUS_MANUAL


def test_a_channel_without_a_connector_falls_back_to_manual(clip):
    outcome = videopub.publish("linkedin", clip, "caption", publishers={})

    assert outcome["status"] == videopub.STATUS_MANUAL
    assert "linkedin" in outcome["error"]


def test_a_successful_publication_reports_the_url(clip):
    outcome = videopub.publish(
        "linkedin", clip, "caption", publishers={"linkedin": lambda c, t: "https://linkedin.com/p/1"}
    )

    assert outcome["status"] == videopub.STATUS_PUBLISHED
    assert outcome["external_url"] == "https://linkedin.com/p/1"


def test_a_missing_clip_never_reaches_the_api(tmp_path):
    def explode(*args, **kwargs):
        raise AssertionError("a missing file must not be uploaded")

    outcome = videopub.publish("linkedin", tmp_path / "absent.mp4", "caption", publishers={"linkedin": explode})

    assert outcome["status"] == videopub.STATUS_FAILED
    assert outcome["retryable"] is False


def test_a_publisher_raising_an_expired_token_is_classified(clip):
    def expired(c, t):
        raise videopub.PublicationError("token expired", status_code=401)

    outcome = videopub.publish("linkedin", clip, "caption", publishers={"linkedin": expired})

    assert outcome["status"] == videopub.STATUS_NEEDS_TOKEN
    assert outcome["retryable"] is False


def test_an_unexpected_exception_never_escapes(clip):
    def boom(c, t):
        raise RuntimeError("something else entirely")

    outcome = videopub.publish("linkedin", clip, "caption", publishers={"linkedin": boom})

    assert outcome["status"] == videopub.STATUS_FAILED
    assert "something else" in outcome["error"]


def test_the_linkedin_clip_is_uploaded_as_a_video(clip):
    calls = {}

    class FakeApi:
        @staticmethod
        def _upload_linkedin_asset(path, *, recipe, label):
            calls["recipe"] = recipe
            calls["path"] = path
            return "urn:li:digitalmediaAsset:1"

        @staticmethod
        def _publish_linkedin_post(text, media_asset=None, media_category="IMAGE"):
            calls["category"] = media_category
            calls["asset"] = media_asset
            calls["text"] = text
            return {"id": "urn:li:share:99"}

    url = videopub.publish_linkedin_video(FakeApi, clip, "caption")

    assert calls["recipe"].endswith("feedshare-video")
    assert calls["category"] == "VIDEO"
    assert "urn:li:share:99" in url


def fake_x_api(responses, calls):
    class FakeSession:
        def post(self, url, headers=None, files=None, json=None, timeout=None):
            calls.append(("POST", url, json))
            return responses.pop(0)

        def get(self, url, headers=None, timeout=None):
            calls.append(("GET", url, None))
            return responses.pop(0)

    class FakeApi:
        X_API_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
        X_API_TWEET_URL = "https://api.x.com/2/tweets"

        @staticmethod
        def _build_oauth1_header(method, url):
            return f"OAuth {method}"

    return FakeApi, FakeSession()


def test_the_x_clip_goes_through_init_append_finalize_then_the_tweet(clip):
    calls = []
    responses = [
        FakeResponse(payload={"media_id_string": "77"}),          # INIT
        FakeResponse(payload={}),                                  # APPEND
        FakeResponse(payload={"processing_info": {"state": "succeeded"}}),  # FINALIZE
        FakeResponse(payload={"data": {"id": "1234"}}),            # tweet
    ]
    api, session = fake_x_api(responses, calls)

    url = videopub.publish_x_video(api, clip, "caption", session=session, sleeper=lambda _: None)

    commands = [call[1] for call in calls]
    assert "command=INIT" in commands[0]
    assert "command=APPEND" in commands[1]
    assert "command=FINALIZE" in commands[2]
    assert calls[3][1] == api.X_API_TWEET_URL
    assert "1234" in url


def test_the_tweet_waits_for_x_to_finish_processing(clip):
    calls = []
    responses = [
        FakeResponse(payload={"media_id_string": "77"}),
        FakeResponse(payload={}),
        FakeResponse(payload={"processing_info": {"state": "in_progress", "check_after_secs": 1}}),
        FakeResponse(payload={"processing_info": {"state": "succeeded"}}),
        FakeResponse(payload={"data": {"id": "1234"}}),
    ]
    api, session = fake_x_api(responses, calls)

    videopub.publish_x_video(api, clip, "caption", session=session, sleeper=lambda _: None)

    assert any("command=STATUS" in call[1] for call in calls)


def test_a_video_x_rejects_is_reported_without_a_tweet(clip):
    calls = []
    responses = [
        FakeResponse(payload={"media_id_string": "77"}),
        FakeResponse(payload={}),
        FakeResponse(payload={"processing_info": {"state": "failed", "error": {"message": "trop long"}}}),
    ]
    api, session = fake_x_api(responses, calls)

    with pytest.raises(videopub.PublicationError, match="trop long"):
        videopub.publish_x_video(api, clip, "caption", session=session, sleeper=lambda _: None)

    assert not any(call[1] == api.X_API_TWEET_URL for call in calls)


def test_an_expired_x_token_surfaces_its_status(clip):
    calls = []
    responses = [FakeResponse(status_code=401, text="Unauthorized")]
    api, session = fake_x_api(responses, calls)

    with pytest.raises(videopub.PublicationError) as excinfo:
        videopub.publish_x_video(api, clip, "caption", session=session, sleeper=lambda _: None)

    assert excinfo.value.status_code == 401
    assert videopub.classify(excinfo.value.status_code, str(excinfo.value))[0] == videopub.STATUS_NEEDS_TOKEN
