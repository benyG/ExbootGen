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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, quote, urlparse

import mysql.connector
import requests
from flask import Blueprint, jsonify, render_template, request

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
    generate_certification_course_art,
    generate_certification_linkedin_post,
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

TOPIC_TYPE_DB_VALUES = {
    "certification_presentation": 1,
    "preparation_methodology": 2,
    "experience_testimony": 3,
    "career_impact": 4,
    "engagement_community": 5,
}

TOPIC_TYPE_TEXT_LABELS = {
    option["value"]: option["label"].split(" ", 1)[1]
    if " " in option["label"]
    else option["label"]
    for option in TOPIC_TYPE_OPTIONS
}

COURSE_ART_TOPIC = "certification_presentation"


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


def _get_existing_presentation_blog_id(certification_id: int) -> Optional[int]:
    """Return the existing presentation blog identifier for the certification."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT b.id
            FROM blog_courses bc
            JOIN blogs b ON bc.blog = b.id
            WHERE bc.course = %s AND b.topic_type = %s
            ORDER BY b.updated_at DESC
            LIMIT 1
            """,
            (certification_id, TOPIC_TYPE_DB_VALUES[COURSE_ART_TOPIC]),
        )
        row = cursor.fetchone()
        return int(row[0]) if row else None
    finally:
        cursor.close()
        conn.close()


def _map_topic_type_to_db_value(topic_type: str) -> int:
    """Translate the topic type identifier to its database value."""

    try:
        return TOPIC_TYPE_DB_VALUES[topic_type]
    except KeyError as exc:  # pragma: no cover - defensive programming
        raise ValueError("Type de sujet inconnu pour l'enregistrement.") from exc


def _build_article_summary(article_text: str) -> str:
    """Construct a concise summary from the generated article."""

    paragraphs = [line.strip() for line in article_text.splitlines() if line.strip()]
    if not paragraphs:
        return ""

    summary_parts = []
    current_length = 0
    for paragraph in paragraphs:
        summary_parts.append(paragraph)
        current_length += len(paragraph)
        if current_length >= 400:
            break

    summary = " ".join(summary_parts)
    summary = summary.strip()
    if len(summary) > 500:
        summary = summary[:497].rstrip() + "…"
    return summary


def _persist_blog_article(
    title: str,
    topic_type_value: int,
    summary: str,
    article_text: str,
    exam_url: str,
    certification_id: int,
) -> int:
    """Insert the blog article and its course association in the database."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        conn.start_transaction()
        cursor.execute(
            """
            INSERT INTO blogs (title, topic_type, res, article, url, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
            """,
            # Keep `res` and `url` empty in storage per the product requirement.
            (title, topic_type_value, "", article_text, ""),
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
        return int(blog_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _save_course_art_json(certification_id: int, course_art_payload: dict) -> None:
    """Persist the structured course description for the certification."""

    course_art_json = json.dumps(course_art_payload, ensure_ascii=False)
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE courses SET art = %s WHERE id = %s",
            (course_art_json, certification_id),
        )
        conn.commit()
    except mysql.connector.Error:
        conn.rollback()
        raise
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
        exam_url = "http://examboot.net"
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
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type == COURSE_ART_TOPIC:
        existing_blog_id = _get_existing_presentation_blog_id(certification_id)
        if existing_blog_id:
            return (
                jsonify(
                    {
                        "error": (
                            f"Un article de présentation existe déjà pour {selection.certification_name}."
                            " Aucun nouvel article n'a été généré."
                        ),
                        "blog_id": existing_blog_id,
                    }
                ),
                409,
            )

    try:
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


@articles_bp.route("/publish-article", methods=["POST"])
def publish_article():
    """Persist the generated article and link it to the certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    article_text = (data.get("article") or "").strip()
    if not article_text:
        return jsonify({"error": "Le contenu de l'article est requis pour la publication."}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type == COURSE_ART_TOPIC:
        existing_blog_id = _get_existing_presentation_blog_id(certification_id)
        if existing_blog_id:
            return (
                jsonify(
                    {
                        "error": (
                            f"Un article de présentation existe déjà pour {selection.certification_name}."
                            " Aucun nouvel article n'a été enregistré."
                        ),
                        "blog_id": existing_blog_id,
                    }
                ),
                409,
            )

    try:
        topic_type_value = _map_topic_type_to_db_value(topic_type)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    title = (data.get("title") or "").strip()
    summary = (data.get("summary") or "").strip()

    if not title:
        topic_label = TOPIC_TYPE_TEXT_LABELS.get(
            topic_type, topic_type.replace("_", " ").title()
        )
        title = f"{selection.certification_name} – {topic_label}"

    if not summary:
        summary = _build_article_summary(article_text)
    if not summary:
        summary = selection.certification_name

    try:
        blog_id = _persist_blog_article(
            title,
            topic_type_value,
            summary,
            article_text,
            exam_url,
            certification_id,
        )
    except mysql.connector.Error as exc:  # pragma: no cover - database error path
        return jsonify({"error": f"Erreur lors de l'enregistrement de l'article: {exc}"}), 500
    except Exception as exc:  # pragma: no cover - defensive fallback
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "blog_id": blog_id,
            "title": title,
            "summary": summary,
            "topic_type": topic_type,
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
        }
    )


@articles_bp.route("/run-playbook", methods=["POST"])
def run_playbook():
    """Run the social playbook: generate content and publish announcements."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type == COURSE_ART_TOPIC:
        existing_blog_id = _get_existing_presentation_blog_id(certification_id)
        if existing_blog_id:
            return (
                jsonify(
                    {
                        "error": (
                            f"Un article de présentation existe déjà pour {selection.certification_name}."
                            " Aucun nouvel article n'a été généré."
                        ),
                        "blog_id": existing_blog_id,
                    }
                ),
                409,
            )

    attach_image = bool(data.get("add_image"))

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            article_future = executor.submit(
                _generate_and_persist_article_task,
                selection,
                exam_url,
                topic_type,
                certification_id,
            )
            article_payload = article_future.result()

            tweet_future = executor.submit(
                _generate_and_publish_tweet_task,
                selection,
                exam_url,
                topic_type,
                attach_image,
            )
            linkedin_future = executor.submit(
                _generate_and_publish_linkedin_task,
                selection,
                exam_url,
                topic_type,
                attach_image,
            )
            course_art_future = (
                executor.submit(
                    _generate_and_store_course_art_task,
                    selection,
                    certification_id,
                )
                if topic_type == COURSE_ART_TOPIC
                else None
            )

            tweet_text, tweet_result = tweet_future.result()
            linkedin_text, linkedin_result = linkedin_future.result()
            course_art_payload = (
                course_art_future.result() if course_art_future else None
            )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    response_payload = {
        "article": article_payload["article"],
        "blog_id": article_payload["blog_id"],
        "title": article_payload["title"],
        "summary": article_payload["summary"],
        "provider_name": selection.provider_name,
        "certification_name": selection.certification_name,
        "tweet": tweet_text,
        "tweet_response": tweet_result.response,
        "tweet_published": tweet_result.published,
        "tweet_status_code": tweet_result.status_code,
        "linkedin_post": linkedin_text,
        "linkedin_response": linkedin_result.response,
        "linkedin_published": linkedin_result.published,
        "linkedin_status_code": linkedin_result.status_code,
    }
    if tweet_result.media_filename:
        response_payload["tweet_image"] = tweet_result.media_filename
    if tweet_result.error:
        response_payload["tweet_error"] = tweet_result.error
    if linkedin_result.media_filename:
        response_payload["linkedin_image"] = linkedin_result.media_filename
    if linkedin_result.error:
        response_payload["linkedin_error"] = linkedin_result.error

    if course_art_payload:
        if course_art_payload.get("course_art") is not None:
            response_payload["course_art"] = course_art_payload["course_art"]
            response_payload["course_art_saved"] = True
        if course_art_payload.get("error"):
            response_payload["course_art_error"] = course_art_payload["error"]

    return jsonify(response_payload)


def _generate_and_persist_article_task(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    certification_id: int,
) -> dict:
    """Generate the article text and persist it to the database."""

    article_text = generate_certification_article(
        selection.certification_name,
        selection.provider_name,
        exam_url,
        topic_type,
    )
    topic_type_value = _map_topic_type_to_db_value(topic_type)
    topic_label = TOPIC_TYPE_TEXT_LABELS.get(
        topic_type, topic_type.replace("_", " ").title()
    )
    title = f"{selection.certification_name} – {topic_label}"
    summary = _build_article_summary(article_text)
    if not summary:
        summary = selection.certification_name

    blog_id = _persist_blog_article(
        title,
        topic_type_value,
        summary,
        article_text,
        exam_url,
        certification_id,
    )

    return {
        "article": article_text,
        "blog_id": blog_id,
        "title": title,
        "summary": summary,
    }


def _generate_and_publish_tweet_task(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool,
) -> Tuple[str, SocialPostResult]:
    """Generate the tweet content and trigger its publication."""

    tweet_text = generate_certification_tweet(
        selection.certification_name,
        selection.provider_name,
        exam_url,
        topic_type,
    )
    tweet_result = _run_tweet_workflow(
        selection,
        exam_url,
        topic_type,
        attach_image=attach_image,
        tweet_text=tweet_text,
    )
    return tweet_text, tweet_result


def _generate_and_publish_linkedin_task(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool,
) -> Tuple[str, SocialPostResult]:
    """Generate the LinkedIn post content and trigger its publication."""

    linkedin_post = generate_certification_linkedin_post(
        selection.certification_name,
        selection.provider_name,
        exam_url,
        topic_type,
    )
    linkedin_result = _run_linkedin_workflow(
        selection,
        exam_url,
        topic_type,
        attach_image=attach_image,
        linkedin_post=linkedin_post,
    )
    return linkedin_post, linkedin_result


def _generate_and_store_course_art_task(
    selection: Selection,
    certification_id: int,
) -> dict:
    """Generate the course art JSON and persist it when possible."""

    try:
        course_art = generate_certification_course_art(
            selection.certification_name,
            selection.provider_name,
        )
        _save_course_art_json(certification_id, course_art)
        return {"course_art": course_art}
    except Exception as exc:  # pragma: no cover - surfaced to the caller
        return {"course_art": None, "error": str(exc)}


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


@articles_bp.route("/generate-course-art", methods=["POST"])
def generate_course_art():
    """Generate the structured course information when available."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type != COURSE_ART_TOPIC:
        return (
            jsonify(
                {
                    "error": "La fiche certification ne peut être générée que pour une présentation de certification.",
                }
            ),
            400,
        )

    try:
        selection = _fetch_selection(provider_id, certification_id)
        course_art = generate_certification_course_art(
            selection.certification_name,
            selection.provider_name,
        )
    except Exception as exc:  # pragma: no cover - propagated for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"course_art": course_art})


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

    payload = {
        "tweet": tweet_result.text,
        "tweet_response": tweet_result.response,
        "tweet_published": tweet_result.published,
        "tweet_status_code": tweet_result.status_code,
    }
    if tweet_result.media_filename:
        payload["tweet_image"] = tweet_result.media_filename
    if tweet_result.error:
        payload["tweet_error"] = tweet_result.error

    return jsonify(payload)


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

    payload = {
        "linkedin_post": linkedin_result.text,
        "linkedin_response": linkedin_result.response,
        "linkedin_published": linkedin_result.published,
        "linkedin_status_code": linkedin_result.status_code,
    }
    if linkedin_result.media_filename:
        payload["linkedin_image"] = linkedin_result.media_filename
    if linkedin_result.error:
        payload["linkedin_error"] = linkedin_result.error

    return jsonify(payload)


@articles_bp.route("/publish-course-art", methods=["POST"])
def publish_course_art():
    """Persist the generated course JSON to the certification record."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    if topic_type != COURSE_ART_TOPIC:
        return (
            jsonify(
                {
                    "error": "La publication de la fiche n'est possible que pour le type présentation de certification.",
                }
            ),
            400,
        )

    course_art_payload = data.get("course_art")
    if isinstance(course_art_payload, str):
        try:
            course_art_payload = json.loads(course_art_payload)
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"JSON de fiche invalide: {exc}"}), 400

    if not isinstance(course_art_payload, dict):
        return jsonify({"error": "Le contenu de la fiche doit être un objet JSON."}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        _save_course_art_json(certification_id, course_art_payload)
    except mysql.connector.Error as exc:  # pragma: no cover - database error path
        return jsonify({"error": f"Erreur lors de l'enregistrement de la fiche: {exc}"}), 500
    except Exception as exc:  # pragma: no cover - defensive fallback
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "course_art": course_art_payload,
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
        }
    )
