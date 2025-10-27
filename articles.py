"""Blueprint implementing the certification article generator workflow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import random
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, quote, urlparse

import mysql.connector
import requests
from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from config import (
    DB_CONFIG,
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_ACCESS_TOKEN_URL,
    LINKEDIN_ASSET_REGISTER_URL,
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
    LINKEDIN_ORGANIZATION_URN,
    LINKEDIN_POST_URL,
    LINKEDIN_REFRESH_TOKEN,
    X_API_ACCESS_TOKEN,
    X_API_ACCESS_TOKEN_SECRET,
    X_API_CONSUMER_KEY,
    X_API_CONSUMER_SECRET,
    X_API_MEDIA_UPLOAD_URL,
    X_API_TWEET_URL,
)
from openai_api import (
    generate_certification_article,
    generate_certification_linkedin_post,
    generate_certification_presentation_brief,
    generate_certification_tweet,
)

articles_bp = Blueprint("articles", __name__)


TOPIC_TYPE_OPTIONS = [
    {"value": "certification_presentation", "label": "🎯 Certification presentation"},
    {"value": "preparation_methodology", "label": "🧠 Preparation & methodology"},
    {"value": "experience_testimony", "label": "💬 Experience & testimony"},
    {"value": "career_impact", "label": "📊 Career & impact"},
    {"value": "engagement_community", "label": "🧩 Engagement & community"},
]

TOPIC_TYPE_VALUES = {option["value"] for option in TOPIC_TYPE_OPTIONS}
TOPIC_TYPE_LABELS = {option["value"]: option["label"] for option in TOPIC_TYPE_OPTIONS}
TOPIC_TYPE_CODES = {
    "certification_presentation": 1,
    "preparation_methodology": 2,
    "experience_testimony": 3,
    "career_impact": 4,
    "engagement_community": 5,
}


@dataclass
class Selection:
    """Container for the provider and certification names selected by the user."""

    provider_name: str
    certification_name: str


@dataclass
class SocialPostResult:
    """Outcome of a social network publication attempt."""

    text: str
    response: Optional[dict] = None
    published: bool = False
    status_code: Optional[int] = None
    error: Optional[str] = None
    media_filename: Optional[str] = None


SOCIAL_IMAGE_DIR = Path(__file__).resolve().parent / "images"
SOCIAL_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class SocialImageError(RuntimeError):
    """Raised when a social image cannot be selected."""

class SocialPublishError(RuntimeError):
    """Exception raised when a social network publication fails.

    It carries the HTTP status returned by the upstream API so the route can
    propagate a meaningful status code back to the front-end instead of a
    generic 500 error.
    """

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _serialize_social_result(
    prefix: str, result: Optional[SocialPostResult]
) -> dict[str, object]:
    """Return a JSON-serialisable payload for a social publication result."""

    if not result:
        return {}

    payload: dict[str, object] = {
        prefix: result.text,
        f"{prefix}_response": result.response,
        f"{prefix}_published": result.published,
        f"{prefix}_status_code": result.status_code,
    }

    if result.media_filename:
        payload[f"{prefix}_image"] = result.media_filename
    if result.error:
        payload[f"{prefix}_error"] = result.error

    return payload


def _fetch_selection(provider_id: int, certification_id: int) -> Selection:
    """Return the provider and certification names for the given identifiers."""

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT name FROM provs WHERE id = %s", (provider_id,))
        provider_row = cur.fetchone()
        if not provider_row:
            raise ValueError("Provider introuvable.")

        cur.execute(
            "SELECT name FROM courses WHERE id = %s AND prov = %s",
            (certification_id, provider_id),
        )
        certification_row = cur.fetchone()
        if not certification_row:
            raise ValueError("Certification introuvable pour ce provider.")
    finally:
        conn.close()

    return Selection(
        provider_name=provider_row["name"],
        certification_name=certification_row["name"],
    )


def _derive_article_title(article: str, selection: Selection, topic_type: str) -> str:
    """Return a title for the blog post using the article body as source."""

    first_meaningful_line = ""
    for line in article.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        first_meaningful_line = stripped.lstrip("# ").strip()
        if first_meaningful_line:
            break

    if not first_meaningful_line:
        topic_label = TOPIC_TYPE_LABELS.get(topic_type, "")
        if topic_label:
            # Remove the leading emoji (if present) to avoid storing it twice.
            parts = topic_label.split(" ", 1)
            topic_label = parts[1] if len(parts) > 1 else parts[0]
        fallback_title = selection.certification_name
        if topic_label:
            fallback_title = f"{fallback_title} · {topic_label}"
        first_meaningful_line = fallback_title

    return first_meaningful_line[:500]


def _summarize_article(article: str) -> str:
    """Return a compact summary used to populate the legacy ``res`` column."""

    collapsed = " ".join(article.split())
    summary = collapsed[:1000]
    return summary or "Article généré automatiquement."


def _persist_article(
    selection: Selection,
    certification_id: int,
    exam_url: str,
    topic_type: str,
    article: str,
) -> Tuple[int, str]:
    """Insert the generated article and link it to the certification."""

    if topic_type not in TOPIC_TYPE_CODES:
        raise ValueError("Type de sujet invalide.")

    clean_article = article.strip()
    if not clean_article:
        raise ValueError("Le contenu de l'article est vide.")

    topic_code = TOPIC_TYPE_CODES[topic_type]
    title = _derive_article_title(clean_article, selection, topic_type)
    summary = _summarize_article(clean_article)
    url_value = exam_url.strip() or None

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO blogs (title, topic_type, img, res, article, url, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (title, topic_code, None, summary, clean_article, url_value),
        )
        blog_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO blog_courses (blog, course, created_at, updated_at)
            VALUES (%s, %s, NOW(), NOW())
            """,
            (blog_id, certification_id),
        )
        conn.commit()
    except mysql.connector.Error as exc:
        conn.rollback()
        raise RuntimeError(
            "Erreur lors de l'enregistrement de l'article en base de données."
        ) from exc
    finally:
        cursor.close()
        conn.close()

    return blog_id, title


def _persist_certification_brief(
    provider_id: int,
    certification_id: int,
    brief_payload: dict,
) -> None:
    """Store the certification brief JSON in the ``courses.art`` column."""

    serialized = json.dumps(brief_payload, ensure_ascii=False)

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE courses SET art = %s WHERE id = %s AND prov = %s",
            (serialized, certification_id, provider_id),
        )
        if cursor.rowcount == 0:
            raise RuntimeError(
                "Impossible de mettre à jour la fiche certification (cours introuvable)."
            )
        conn.commit()
    except mysql.connector.Error as exc:
        conn.rollback()
        raise RuntimeError(
            "Erreur lors de l'enregistrement de la fiche certification."
        ) from exc
    finally:
        cursor.close()
        conn.close()


def _percent_encode(value: str) -> str:
    """Return a string percent-encoded according to RFC 3986."""

    return quote(str(value), safe="~-._")


def _normalize_base_url(url_parts: ParseResult) -> str:
    """Return the normalized base string URI as defined by RFC 5849."""

    scheme = (url_parts.scheme or "").lower()
    hostname = (url_parts.hostname or "").lower()

    if not scheme or not hostname:
        raise ValueError("L'URL fournie pour la signature OAuth est invalide.")

    port = url_parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        authority = f"{hostname}:{port}"
    else:
        authority = hostname

    path = url_parts.path or "/"

    return f"{scheme}://{authority}{path}"


def _build_oauth1_header(method: str, url: str) -> str:
    """Return the OAuth 1.0 Authorization header for the given request."""

    nonce = secrets.token_hex(16)
    timestamp = str(int(time.time()))

    oauth_params = {
        "oauth_consumer_key": X_API_CONSUMER_KEY,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp,
        "oauth_token": X_API_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }

    url_parts = urlparse(url)
    query_params = parse_qsl(url_parts.query, keep_blank_values=True)

    signature_pairs_raw = list(query_params) + list(oauth_params.items())
    encoded_signature_pairs = [
        (_percent_encode(key), _percent_encode(value))
        for key, value in signature_pairs_raw
    ]
    encoded_signature_pairs.sort(key=lambda item: (item[0], item[1]))
    parameter_string = "&".join(
        f"{key}={value}" for key, value in encoded_signature_pairs
    )

    base_url = _normalize_base_url(url_parts)
    base_string = "&".join(
        _percent_encode(part)
        for part in (method.upper(), base_url, parameter_string)
    )

    signing_key = "&".join(
        (_percent_encode(X_API_CONSUMER_SECRET), _percent_encode(X_API_ACCESS_TOKEN_SECRET))
    )
    signature = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    oauth_params["oauth_signature"] = base64.b64encode(signature).decode("utf-8")

    header_params = ", ".join(
        f'{_percent_encode(key)}="{_percent_encode(value)}"'
        for key, value in sorted(oauth_params.items())
    )
    return f"OAuth {header_params}"


def _list_social_images() -> list[Path]:
    """Return the list of social images available on disk."""

    if not SOCIAL_IMAGE_DIR.exists():
        return []

    return [
        path
        for path in SOCIAL_IMAGE_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SOCIAL_IMAGE_EXTENSIONS
    ]


def _pick_random_social_image() -> Path:
    """Return a random image path from the social images directory."""

    images = _list_social_images()
    if not images:
        raise SocialImageError(
            "Aucune image n'est disponible dans le dossier 'images'. Ajoutez des fichiers "
            "(.png, .jpg, .jpeg, .gif ou .webp) pour activer cette fonctionnalité."
        )

    return random.choice(images)


def render_x_callback() -> str:
    """Render the callback landing page for the X OAuth 2.0 flow."""

    code = request.args.get("code")
    state = request.args.get("state")
    error = request.args.get("error")
    error_description = request.args.get("error_description")

    return render_template(
        "x_callback.html",
        code=code,
        state=state,
        error=error,
        error_description=error_description,
    )


@articles_bp.route("/x/callback")
def articles_x_callback() -> str:
    """Expose the callback through the articles blueprint for completeness."""

    return render_x_callback()


def _upload_twitter_media(image_path: Path) -> str:
    """Upload an image to X (Twitter) and return the media identifier."""

    if not image_path.exists():
        raise SocialPublishError(
            f"Le fichier image '{image_path}' est introuvable.", status_code=400
        )

    headers = {
        "Authorization": _build_oauth1_header("POST", X_API_MEDIA_UPLOAD_URL),
    }

    with image_path.open("rb") as file_handle:
        response = requests.post(
            X_API_MEDIA_UPLOAD_URL,
            headers=headers,
            files={"media": file_handle},
            timeout=30,
        )

    if response.status_code >= 400:
        raise SocialPublishError(
            "Erreur lors du téléversement de l'image sur X "
            f"({response.status_code}): {response.text}",
            status_code=response.status_code,
        )

    payload = response.json()
    media_id = payload.get("media_id_string") or payload.get("media_id")
    if not media_id:
        raise SocialPublishError(
            "Réponse inattendue de l'API X lors du téléversement de l'image.",
            status_code=response.status_code or 500,
        )

    return str(media_id)


def _publish_tweet(text: str, media_path: Optional[Path] = None) -> dict:
    """Publish a tweet using the X (Twitter) v2 API."""

    if not text.strip():
        raise ValueError("Le contenu du tweet est vide.")

    oauth1_credentials = all(
        (
            X_API_CONSUMER_KEY,
            X_API_CONSUMER_SECRET,
            X_API_ACCESS_TOKEN,
            X_API_ACCESS_TOKEN_SECRET,
        )
    )

    if not oauth1_credentials:
        raise RuntimeError(
            "Les identifiants X (Twitter) sont incomplets. Fournissez les clés OAuth 1.0a "
            "(X_API_CONSUMER_KEY, X_API_CONSUMER_SECRET, X_API_ACCESS_TOKEN, "
            "X_API_ACCESS_TOKEN_SECRET)."
        )

    media_ids = None
    if media_path:
        media_ids = [_upload_twitter_media(media_path)]

    headers = {
        "Authorization": _build_oauth1_header("POST", X_API_TWEET_URL),
        "Content-Type": "application/json",
    }

    payload = {"text": text}
    if media_ids:
        payload["media"] = {"media_ids": media_ids}

    response = requests.post(
        X_API_TWEET_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )

    if response.status_code >= 400:
        error_message = response.text
        if response.status_code == 403 and "Unsupported Authentication" in error_message:
            error_message = (
                "L'API X a rejeté l'authentification utilisée. L'envoi de tweets "
                "nécessite désormais des identifiants OAuth 1.0a (user context). "
                "Vérifiez la configuration des variables X_API_CONSUMER_KEY, "
                "X_API_CONSUMER_SECRET, X_API_ACCESS_TOKEN et "
                "X_API_ACCESS_TOKEN_SECRET."
            )
        raise SocialPublishError(
            f"Erreur lors de la publication du tweet ({response.status_code}): {error_message}",
            status_code=response.status_code,
        )

    return response.json()


_LINKEDIN_ACCESS_TOKEN_CACHE: Optional[str] = None


def _get_linkedin_access_token(force_refresh: bool = False) -> str:
    """Return a valid LinkedIn access token, refreshing it when possible."""

    global _LINKEDIN_ACCESS_TOKEN_CACHE

    if force_refresh:
        _LINKEDIN_ACCESS_TOKEN_CACHE = None
    elif _LINKEDIN_ACCESS_TOKEN_CACHE:
        return _LINKEDIN_ACCESS_TOKEN_CACHE

    if not force_refresh and LINKEDIN_ACCESS_TOKEN:
        _LINKEDIN_ACCESS_TOKEN_CACHE = LINKEDIN_ACCESS_TOKEN
        return _LINKEDIN_ACCESS_TOKEN_CACHE

    if not LINKEDIN_REFRESH_TOKEN:
        if LINKEDIN_ACCESS_TOKEN:
            raise RuntimeError(
                "Le token LinkedIn configuré est expiré et aucun LINKEDIN_REFRESH_TOKEN n'est disponible pour le renouveler."
            )
        raise RuntimeError(
            "Aucun jeton LinkedIn n'est configuré. Fournissez LINKEDIN_ACCESS_TOKEN ou un couple refresh token + identifiants OAuth."
        )

    if not (LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET):
        raise RuntimeError(
            "Les identifiants OAuth LinkedIn sont requis pour rafraîchir le token. Configurez LINKEDIN_CLIENT_ID et LINKEDIN_CLIENT_SECRET."
        )

    response = requests.post(
        LINKEDIN_ACCESS_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": LINKEDIN_REFRESH_TOKEN,
            "client_id": LINKEDIN_CLIENT_ID,
            "client_secret": LINKEDIN_CLIENT_SECRET,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            "Impossible d'obtenir un access token LinkedIn: "
            f"{response.status_code} {response.text}"
        )

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError("Réponse LinkedIn invalide: access_token manquant.")

    _LINKEDIN_ACCESS_TOKEN_CACHE = token
    return token


def _upload_linkedin_image(image_path: Path) -> str:
    """Upload an image to LinkedIn and return the asset URN."""

    if not image_path.exists():
        raise SocialPublishError(
            f"Le fichier image '{image_path}' est introuvable.", status_code=400
        )

    if not LINKEDIN_ORGANIZATION_URN:
        raise SocialPublishError(
            "LINKEDIN_ORGANIZATION_URN doit être configuré pour envoyer des images LinkedIn.",
            status_code=400,
        )

    image_bytes = image_path.read_bytes()
    mime_type, _ = mimetypes.guess_type(str(image_path))
    content_type = mime_type or "application/octet-stream"

    last_error: Optional[SocialPublishError] = None
    for force_refresh in (False, True):
        token = _get_linkedin_access_token(force_refresh=force_refresh)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        register_response = requests.post(
            LINKEDIN_ASSET_REGISTER_URL,
            headers=headers,
            json={
                "registerUploadRequest": {
                    "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                    "owner": LINKEDIN_ORGANIZATION_URN,
                    "serviceRelationships": [
                        {
                            "relationshipType": "OWNER",
                            "identifier": "urn:li:userGeneratedContent",
                        }
                    ],
                }
            },
            timeout=30,
        )

        if register_response.status_code == 401 and not force_refresh:
            continue
        if register_response.status_code >= 400:
            last_error = SocialPublishError(
                "Erreur lors de l'enregistrement de l'image LinkedIn "
                f"({register_response.status_code}): {register_response.text}",
                status_code=register_response.status_code,
            )
            break

        value = register_response.json().get("value", {})
        asset_urn = value.get("asset")
        upload_mechanism = value.get("uploadMechanism", {})
        upload_request = upload_mechanism.get(
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest", {}
        )
        upload_url = upload_request.get("uploadUrl")

        if not asset_urn or not upload_url:
            last_error = SocialPublishError(
                "Réponse LinkedIn invalide lors de l'enregistrement de l'image.",
                status_code=register_response.status_code or 500,
            )
            break

        upload_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        }
        upload_response = requests.put(
            upload_url,
            headers=upload_headers,
            data=image_bytes,
            timeout=30,
        )

        if upload_response.status_code == 401 and not force_refresh:
            continue
        if upload_response.status_code >= 400:
            last_error = SocialPublishError(
                "Erreur lors du téléversement de l'image sur LinkedIn "
                f"({upload_response.status_code}): {upload_response.text}",
                status_code=upload_response.status_code,
            )
            break

        return asset_urn

    if last_error:
        raise last_error

    raise SocialPublishError(
        "Impossible de téléverser l'image sur LinkedIn après nouvelle tentative.",
        status_code=500,
    )


def _publish_linkedin_post(text: str, media_asset: Optional[str] = None) -> dict:
    """Publish a post to the configured LinkedIn organisation page."""

    if not text.strip():
        raise ValueError("Le contenu LinkedIn est vide.")

    if not LINKEDIN_ORGANIZATION_URN:
        raise RuntimeError(
            "LINKEDIN_ORGANIZATION_URN n'est pas configuré pour identifier la page LinkedIn."
        )

    def _send(access_token: str) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        share_content = {
            "shareCommentary": {"text": text},
            "shareMediaCategory": "IMAGE" if media_asset else "NONE",
        }
        if media_asset:
            share_content["media"] = [
                {
                    "status": "READY",
                    "media": media_asset,
                    "title": {"text": "Publication ExBoot"},
                }
            ]
        payload = {
            "author": LINKEDIN_ORGANIZATION_URN,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": share_content},
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }
        return requests.post(LINKEDIN_POST_URL, headers=headers, json=payload, timeout=30)

    token = _get_linkedin_access_token()
    response = _send(token)

    if response.status_code == 401:
        # Token expired: attempt a refresh if possible.
        token = _get_linkedin_access_token(force_refresh=True)
        response = _send(token)

    if response.status_code >= 400:
        raise SocialPublishError(
            f"Erreur lors de la publication LinkedIn ({response.status_code}): {response.text}",
            status_code=response.status_code,
        )

    return response.json()


@articles_bp.route("/")
def index() -> str:
    """Render the article generator interface."""

    return render_template(
        "article_generator.html",
        topic_types=TOPIC_TYPE_OPTIONS,
    )


def _extract_selection_payload(data: dict) -> Tuple[int, int, str, str]:
    """Return the validated identifiers and URL from the request payload."""

    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")
    exam_url = (data.get("exam_url") or "").strip()
    if not exam_url:
        exam_url = "https://examboot.net"
    topic_type = (data.get("topic_type") or "").strip()

    if not provider_id or not certification_id or not topic_type:
        raise ValueError(
            "provider_id, certification_id et topic_type sont requis."
        )

    try:
        provider_id = int(provider_id)
        certification_id = int(certification_id)
    except (TypeError, ValueError) as exc:  # pragma: no cover - validation only
        raise ValueError("Identifiants invalides.") from exc

    if topic_type not in TOPIC_TYPE_VALUES:
        raise ValueError("Type de sujet invalide.")

    return provider_id, certification_id, exam_url, topic_type


@articles_bp.route("/generate", methods=["POST"])
def generate_article():
    """Generate the certification article using the OpenAI API."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        article = generate_certification_article(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "article": article,
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
        }
    )


@articles_bp.route("/generate-brief", methods=["POST"])
def generate_certification_brief():
    """Generate the certification presentation brief as JSON."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, _exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type != "certification_presentation":
        return (
            jsonify(
                {
                    "error": "La fiche JSON n'est disponible que pour le type 'certification_presentation'."
                }
            ),
            400,
        )

    try:
        selection = _fetch_selection(provider_id, certification_id)
        brief = generate_certification_presentation_brief(
            selection.certification_name,
            selection.provider_name,
        )
    except Exception as exc:  # pragma: no cover - surfaced to clients
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "brief": brief,
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
        }
    )


@articles_bp.route("/publish", methods=["POST"])
def publish_article():
    """Persist the generated article and link it to the certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    article = (data.get("article") or "").strip()
    if not article:
        return jsonify({"error": "Le contenu de l'article est requis pour la publication."}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        blog_id, title = _persist_article(
            selection,
            certification_id,
            exam_url,
            topic_type,
            article,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - defensive fallback
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "blog_id": blog_id,
            "title": title,
            "topic_type": topic_type,
        }
    )


@articles_bp.route("/publish-brief", methods=["POST"])
def publish_certification_brief():
    """Store the certification presentation brief into the course record."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, _exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type != "certification_presentation":
        return (
            jsonify(
                {
                    "error": "La publication du JSON est réservée au type 'certification_presentation'."
                }
            ),
            400,
        )

    brief_payload = data.get("brief")
    if isinstance(brief_payload, str):
        try:
            brief_payload = json.loads(brief_payload)
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"JSON invalide fourni: {exc}"}), 400

    if not isinstance(brief_payload, dict):
        return jsonify({"error": "La fiche JSON fournie est invalide."}), 400

    expected_keys = {"prerequisites", "targeted_profession", "studytip"}
    missing = expected_keys.difference(brief_payload.keys())
    if missing:
        return (
            jsonify(
                {
                    "error": "Clés manquantes dans la fiche JSON: "
                    + ", ".join(sorted(missing))
                }
            ),
            400,
        )

    prerequisites = brief_payload.get("prerequisites")
    targeted_profession = brief_payload.get("targeted_profession")
    studytip = brief_payload.get("studytip")

    if not isinstance(prerequisites, list):
        return jsonify({"error": "Le champ 'prerequisites' doit être une liste."}), 400

    if not isinstance(targeted_profession, list):
        return jsonify({"error": "Le champ 'targeted_profession' doit être une liste."}), 400

    if not isinstance(studytip, str):
        return jsonify({"error": "Le champ 'studytip' doit être une chaîne de caractères."}), 400

    cleaned_prerequisites = [str(item).strip() for item in prerequisites if str(item).strip()]
    cleaned_targeted_profession = [
        str(item).strip()
        for item in targeted_profession
        if str(item).strip()
    ]
    cleaned_studytip = studytip.strip()

    cleaned_prerequisites = cleaned_prerequisites[:3]
    cleaned_targeted_profession = cleaned_targeted_profession[:3]

    if not cleaned_prerequisites or not cleaned_targeted_profession or not cleaned_studytip:
        return jsonify({"error": "Le JSON doit contenir des valeurs pour toutes les clés."}), 400

    brief_payload["prerequisites"] = cleaned_prerequisites
    brief_payload["targeted_profession"] = cleaned_targeted_profession
    brief_payload["studytip"] = cleaned_studytip

    try:
        selection = _fetch_selection(provider_id, certification_id)
        _persist_certification_brief(provider_id, certification_id, brief_payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    except Exception as exc:  # pragma: no cover - defensive fallback
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
            "brief": brief_payload,
        }
    )


@articles_bp.route("/run-playbook", methods=["POST"])
def run_playbook():
    """Run the publication playbook and stream progress updates."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
    except Exception as exc:  # pragma: no cover - propagated for visibility
        return jsonify({"error": str(exc)}), 500

    attach_image = bool(data.get("add_image"))
    include_brief = topic_type == "certification_presentation"
    total_steps = 8 if include_brief else 6

    article_text: str = ""
    blog_id: Optional[int] = None
    blog_title: str = ""
    brief_payload: Optional[dict] = None
    tweet_body: str = ""
    tweet_result: Optional[SocialPostResult] = None
    linkedin_post_body: str = ""
    linkedin_result: Optional[SocialPostResult] = None

    def _ndjson_event(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False) + "\n"

    def event_stream():
        nonlocal article_text, blog_id, blog_title, brief_payload, tweet_body, tweet_result, linkedin_post_body, linkedin_result

        completed_steps = 0

        def progress_event(message: str) -> str:
            label = message
            if completed_steps:
                label = f"Étape {completed_steps}/{total_steps} · {message}"
            progress_value = round(completed_steps / total_steps, 4)
            return _ndjson_event(
                {
                    "type": "progress",
                    "message": label,
                    "step": completed_steps,
                    "total_steps": total_steps,
                    "progress": progress_value,
                }
            )

        try:
            yield _ndjson_event(
                {
                    "type": "progress",
                    "message": "Démarrage du runbook…",
                    "step": completed_steps,
                    "total_steps": total_steps,
                    "progress": 0,
                }
            )

            article_text = generate_certification_article(
                selection.certification_name,
                selection.provider_name,
                exam_url,
                topic_type,
            )
            completed_steps += 1
            yield progress_event("Article généré.")

            blog_id, blog_title = _persist_article(
                selection,
                certification_id,
                exam_url,
                topic_type,
                article_text,
            )
            completed_steps += 1
            article_published_message = "Article publié."
            if blog_id:
                article_published_message = f"Article publié (ID {blog_id})."
            yield progress_event(article_published_message)

            if include_brief:
                brief_payload = generate_certification_presentation_brief(
                    selection.certification_name,
                    selection.provider_name,
                )
                completed_steps += 1
                yield progress_event("Résumé JSON généré.")

                _persist_certification_brief(
                    provider_id,
                    certification_id,
                    brief_payload,
                )
                completed_steps += 1
                yield progress_event(
                    "Résumé JSON publié dans la fiche certification."
                )

            tweet_body = generate_certification_tweet(
                selection.certification_name,
                selection.provider_name,
                exam_url,
                topic_type,
            )
            completed_steps += 1
            yield progress_event("Tweet généré.")

            tweet_result = _run_tweet_workflow(
                selection,
                exam_url,
                topic_type,
                attach_image=attach_image,
                tweet_text=tweet_body,
            )
            if not tweet_result.published:
                error_message = tweet_result.error or "La publication Twitter a échoué."
                raise RuntimeError(error_message)

            completed_steps += 1
            yield progress_event("Tweet publié.")

            linkedin_post_body = generate_certification_linkedin_post(
                selection.certification_name,
                selection.provider_name,
                exam_url,
                topic_type,
            )
            completed_steps += 1
            yield progress_event("Post LinkedIn généré.")

            linkedin_result = _run_linkedin_workflow(
                selection,
                exam_url,
                topic_type,
                attach_image=attach_image,
                linkedin_post=linkedin_post_body,
            )
            if not linkedin_result.published:
                error_message = (
                    linkedin_result.error
                    or "La publication LinkedIn a échoué."
                )
                raise RuntimeError(error_message)

            completed_steps += 1
            yield progress_event("Post LinkedIn publié.")

            completed_steps = total_steps
            yield _ndjson_event(
                {
                    "type": "complete",
                    "message": "Runbook terminé avec succès.",
                    "step": completed_steps,
                    "total_steps": total_steps,
                    "progress": 1.0,
                    "success": True,
                    "provider_name": selection.provider_name,
                    "certification_name": selection.certification_name,
                    "article": article_text,
                    "blog_id": blog_id,
                    "blog_title": blog_title,
                    "brief": brief_payload,
                    "brief_included": include_brief,
                    "brief_published": bool(brief_payload) if include_brief else False,
                    "tweet": tweet_result.text if tweet_result else "",
                    "tweet_published": bool(tweet_result.published)
                    if tweet_result
                    else False,
                    "tweet_status_code": tweet_result.status_code if tweet_result else None,
                    "tweet_error": tweet_result.error if tweet_result else None,
                    "tweet_image": tweet_result.media_filename if tweet_result else None,
                    "tweet_response": tweet_result.response if tweet_result else None,
                    "linkedin_post": linkedin_result.text if linkedin_result else "",
                    "linkedin_published": bool(
                        linkedin_result.published if linkedin_result else False
                    ),
                    "linkedin_status_code": (
                        linkedin_result.status_code if linkedin_result else None
                    ),
                    "linkedin_error": (
                        linkedin_result.error if linkedin_result else None
                    ),
                    "linkedin_image": (
                        linkedin_result.media_filename if linkedin_result else None
                    ),
                    "linkedin_response": (
                        linkedin_result.response if linkedin_result else None
                    ),
                }
            )
        except Exception as exc:  # pragma: no cover - surfaced to clients
            error_message = (
                f"Échec à l'étape {completed_steps + 1}/{total_steps} : {exc}"
                if completed_steps < total_steps
                else str(exc)
            )
            yield _ndjson_event(
                {
                    "type": "error",
                    "message": error_message,
                    "step": completed_steps,
                    "total_steps": total_steps,
                    "progress": round(completed_steps / total_steps, 4)
                    if total_steps
                    else 0,
                }
            )

    headers = {"Cache-Control": "no-cache"}
    return Response(
        stream_with_context(event_stream()),
        mimetype="application/x-ndjson",
        headers=headers,
    )


def _run_tweet_workflow(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool = False,
    tweet_text: Optional[str] = None,
) -> SocialPostResult:
    """Generate and publish the certification announcement tweet."""

    tweet_body = (
        tweet_text
        if tweet_text and tweet_text.strip()
        else generate_certification_tweet(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    )
    media_path: Optional[Path] = None
    media_filename: Optional[str] = None
    if attach_image:
        try:
            media_path = _pick_random_social_image()
            media_filename = media_path.name
        except SocialImageError as exc:
            return SocialPostResult(
                text=tweet_body,
                published=False,
                status_code=400,
                error=str(exc),
            )
    try:
        response = _publish_tweet(tweet_body, media_path=media_path)
    except SocialPublishError as exc:
        return SocialPostResult(
            text=tweet_body,
            published=False,
            status_code=exc.status_code,
            error=str(exc),
            media_filename=media_filename,
        )

    return SocialPostResult(
        text=tweet_body,
        response=response,
        published=True,
        status_code=200,
        media_filename=media_filename,
    )


def _run_linkedin_workflow(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool = False,
    linkedin_post: Optional[str] = None,
) -> SocialPostResult:
    """Generate and publish the LinkedIn announcement post."""

    linkedin_body = (
        linkedin_post
        if linkedin_post and linkedin_post.strip()
        else generate_certification_linkedin_post(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    )
    media_asset: Optional[str] = None
    media_filename: Optional[str] = None
    if attach_image:
        try:
            media_path = _pick_random_social_image()
            media_filename = media_path.name
            media_asset = _upload_linkedin_image(media_path)
        except SocialImageError as exc:
            return SocialPostResult(
                text=linkedin_body,
                published=False,
                status_code=400,
                error=str(exc),
            )
        except SocialPublishError as exc:
            return SocialPostResult(
                text=linkedin_body,
                published=False,
                status_code=exc.status_code,
                error=str(exc),
                media_filename=media_filename,
            )
    try:
        linkedin_response = _publish_linkedin_post(
            linkedin_body, media_asset=media_asset
        )
    except SocialPublishError as exc:
        return SocialPostResult(
            text=linkedin_body,
            published=False,
            status_code=exc.status_code,
            error=str(exc),
            media_filename=media_filename,
        )

    return SocialPostResult(
        text=linkedin_body,
        response=linkedin_response,
        published=True,
        status_code=200,
        media_filename=media_filename,
    )


@articles_bp.route("/generate-tweet", methods=["POST"])
def generate_tweet():
    """Generate the tweet content without publishing it."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_text = generate_certification_tweet(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"tweet": tweet_text})


@articles_bp.route("/generate-linkedin", methods=["POST"])
def generate_linkedin():
    """Generate the LinkedIn post content for the selected certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        linkedin_post = generate_certification_linkedin_post(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"linkedin_post": linkedin_post})


@articles_bp.route("/publish-tweet", methods=["POST"])
def publish_tweet():
    """Generate and publish the announcement tweet."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_result = _run_tweet_workflow(
            selection,
            exam_url,
            topic_type,
            attach_image=bool(data.get("add_image")),
            tweet_text=data.get("tweet"),
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify(_serialize_social_result("tweet", tweet_result))


@articles_bp.route("/publish-linkedin", methods=["POST"])
def publish_linkedin():
    """Generate and publish the LinkedIn announcement post."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        linkedin_result = _run_linkedin_workflow(
            selection,
            exam_url,
            topic_type,
            attach_image=bool(data.get("add_image")),
            linkedin_post=data.get("linkedin_post"),
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify(_serialize_social_result("linkedin", linkedin_result))
