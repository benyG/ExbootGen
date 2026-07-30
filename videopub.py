"""Publishing a clip, automatically where the console holds a token.

LinkedIn and X are already connected to this console, so a clip reaches them
without anyone opening a browser. The short-form feeds — TikTok, Shorts, Reels
— have no credentials here and stay manual: their clip is generated, queued and
downloadable, and the console says so instead of pretending.

The important part is what happens when publishing fails. A token that expired
is not a transient error and retrying it forever only delays the moment someone
is told. Failures are therefore classified: retry what is worth retrying, and
hand back to the admin — with a notification — what is not.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

import requests

#: Feeds the console can post to on its own.
AUTOMATIC_CHANNELS = ("linkedin", "x")

#: Feeds with no credentials here: the clip is queued for a human.
MANUAL_CHANNELS = ("tiktok", "shorts", "reels")

CHANNELS = AUTOMATIC_CHANNELS + MANUAL_CHANNELS

STATUS_PENDING = "pending"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"
STATUS_NEEDS_TOKEN = "needs_token"
STATUS_MANUAL = "manual"

#: Words an API uses when the problem is the credential, not the request.
TOKEN_MARKERS = (
    "invalid_token",
    "invalid token",
    "expired",
    "revoked",
    "unauthorized",
    "not authorized",
    "authentication",
    "insufficient",
    "scope",
)

X_MEDIA_CATEGORY = "tweet_video"
X_STATUS_POLL_SECONDS = 5
X_STATUS_MAX_POLLS = 60
X_CHUNK_BYTES = 4 * 1024 * 1024


class PublicationError(RuntimeError):
    """A publication attempt that did not go through."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def classify(status_code: int, message: str = "") -> tuple[str, bool]:
    """Turn a failure into (status, retryable).

    A credential problem is never retried: no number of attempts renews a
    token, and every silent retry is time the admin is not being told.
    """

    haystack = (message or "").lower()

    if status_code in (401, 403) or any(marker in haystack for marker in TOKEN_MARKERS):
        return STATUS_NEEDS_TOKEN, False

    if status_code == 429 or status_code >= 500 or status_code == 0:
        return STATUS_FAILED, True

    return STATUS_FAILED, False


def publish(channel: str, clip: Path, caption: str, publishers: Optional[dict] = None) -> dict:
    """Attempt one publication and describe the outcome.

    Never raises: the caller records the result whatever it is.
    """

    channel = (channel or "").strip()

    if channel in MANUAL_CHANNELS:
        return {"status": STATUS_MANUAL, "retryable": False, "error": "", "external_url": ""}

    publishers = publishers if publishers is not None else default_publishers()
    publisher = publishers.get(channel)

    if publisher is None:
        return {
            "status": STATUS_MANUAL,
            "retryable": False,
            "error": f"Aucun connecteur automatique pour {channel}.",
            "external_url": "",
        }

    clip = Path(clip)
    if not clip.exists():
        return {
            "status": STATUS_FAILED,
            "retryable": False,
            "error": f"Le clip {clip.name} est introuvable.",
            "external_url": "",
        }

    try:
        external_url = publisher(clip, caption) or ""
    except PublicationError as exc:
        status, retryable = classify(exc.status_code, str(exc))
        return {"status": status, "retryable": retryable, "error": str(exc), "external_url": ""}
    except Exception as exc:  # the API clients raise their own error types
        status, retryable = classify(getattr(exc, "status_code", 0), str(exc))
        return {"status": status, "retryable": retryable, "error": str(exc), "external_url": ""}

    return {"status": STATUS_PUBLISHED, "retryable": False, "error": "", "external_url": external_url}


def default_publishers() -> dict:
    """The real connectors, imported late so this module stays testable."""

    import articles

    return {
        "linkedin": lambda clip, caption: publish_linkedin_video(articles, clip, caption),
        "x": lambda clip, caption: publish_x_video(articles, clip, caption),
    }


def publish_linkedin_video(api, clip: Path, caption: str) -> str:
    """Post the clip on the LinkedIn page, reusing the console's token handling.

    ``api`` is the articles module: its asset upload already refreshes an
    expired token once and raises with the upstream status code, which is
    exactly what the classification above needs.
    """

    asset = api._upload_linkedin_asset(
        Path(clip),
        recipe="urn:li:digitalmediaRecipe:feedshare-video",
        label="vidéo",
    )
    response = api._publish_linkedin_post(caption, media_asset=asset, media_category="VIDEO")

    identifier = (response or {}).get("id") if isinstance(response, dict) else ""

    return f"https://www.linkedin.com/feed/update/{identifier}" if identifier else ""


def publish_x_video(api, clip: Path, caption: str, session=None, sleeper: Optional[Callable] = None) -> str:
    """Post the clip on X: chunked media upload, then the tweet.

    X takes a video in three commands and then processes it asynchronously, so
    the upload is not finished when FINALIZE returns — the status has to be
    polled before the tweet can reference the media.
    """

    session = session or requests
    media_id = _upload_x_video(api, Path(clip), session=session, sleeper=sleeper or time.sleep)

    response = session.post(
        api.X_API_TWEET_URL,
        headers={
            "Authorization": api._build_oauth1_header("POST", api.X_API_TWEET_URL),
            "Content-Type": "application/json",
        },
        json={"text": caption, "media": {"media_ids": [media_id]}},
        timeout=60,
    )

    if response.status_code >= 400:
        raise PublicationError(
            f"Erreur lors de la publication sur X ({response.status_code}): {response.text}",
            status_code=response.status_code,
        )

    identifier = ((response.json() or {}).get("data") or {}).get("id", "")

    return f"https://x.com/i/web/status/{identifier}" if identifier else ""


def _upload_x_video(api, clip: Path, session, sleeper: Callable) -> str:
    binary = clip.read_bytes()

    init = _x_command(
        api,
        session,
        {
            "command": "INIT",
            "total_bytes": str(len(binary)),
            "media_type": "video/mp4",
            "media_category": X_MEDIA_CATEGORY,
        },
    )
    media_id = str(init.get("media_id_string") or init.get("media_id") or "")
    if not media_id:
        raise PublicationError("X n'a pas retourné d'identifiant de média.", status_code=502)

    for index, start in enumerate(range(0, len(binary), X_CHUNK_BYTES)):
        url = _x_url(api, {"command": "APPEND", "media_id": media_id, "segment_index": str(index)})
        response = session.post(
            url,
            headers={"Authorization": api._build_oauth1_header("POST", url)},
            files={"media": binary[start:start + X_CHUNK_BYTES]},
            timeout=120,
        )
        if response.status_code >= 400:
            raise PublicationError(
                f"Erreur lors de l'envoi du segment {index} sur X ({response.status_code}): {response.text}",
                status_code=response.status_code,
            )

    finalize = _x_command(api, session, {"command": "FINALIZE", "media_id": media_id})
    _wait_for_x_processing(api, session, media_id, finalize, sleeper)

    return media_id


def _wait_for_x_processing(api, session, media_id: str, finalize: dict, sleeper: Callable) -> None:
    info = (finalize or {}).get("processing_info") or {}

    for _ in range(X_STATUS_MAX_POLLS):
        state = info.get("state")

        if state in (None, "succeeded"):
            return
        if state == "failed":
            error = (info.get("error") or {}).get("message", "raison inconnue")
            raise PublicationError(f"X a rejeté la vidéo : {error}", status_code=422)

        sleeper(int(info.get("check_after_secs") or X_STATUS_POLL_SECONDS))
        info = (_x_command(api, session, {"command": "STATUS", "media_id": media_id}, method="GET") or {}).get(
            "processing_info"
        ) or {}

    raise PublicationError("X n'a pas terminé le traitement de la vidéo à temps.", status_code=504)


def _x_command(api, session, params: dict, method: str = "POST") -> dict:
    url = _x_url(api, params)
    headers = {"Authorization": api._build_oauth1_header(method, url)}

    response = (session.get if method == "GET" else session.post)(url, headers=headers, timeout=60)

    if response.status_code >= 400:
        raise PublicationError(
            f"Erreur X ({params.get('command')}) ({response.status_code}): {response.text}",
            status_code=response.status_code,
        )

    try:
        return response.json() or {}
    except ValueError:
        return {}


def _x_url(api, params: dict) -> str:
    from urllib.parse import urlencode

    # The parameters travel in the query string so the console's existing OAuth1
    # signer covers them: it signs the URL, not the body.
    return f"{api.X_API_MEDIA_UPLOAD_URL}?{urlencode(params)}"
