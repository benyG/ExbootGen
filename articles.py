"""Blueprint implementing the certification article generator workflow."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import parse_qsl, quote, urlparse

import mysql.connector
import requests
from flask import Blueprint, jsonify, render_template, request

from config import (
    DB_CONFIG,
    LINKEDIN_ACCESS_TOKEN,
    LINKEDIN_ACCESS_TOKEN_URL,
    LINKEDIN_CLIENT_ID,
    LINKEDIN_CLIENT_SECRET,
    LINKEDIN_ORGANIZATION_URN,
    LINKEDIN_POST_URL,
    LINKEDIN_REFRESH_TOKEN,
    X_API_ACCESS_TOKEN,
    X_API_ACCESS_TOKEN_SECRET,
    X_API_CLIENT_ID,
    X_API_CLIENT_SECRET,
    X_API_REFRESH_TOKEN,
    X_API_TOKEN_URL,
    X_API_CONSUMER_KEY,
    X_API_CONSUMER_SECRET,
    X_API_TWEET_URL,
)
from openai_api import (
    generate_certification_article,
    generate_certification_linkedin_post,
    generate_certification_tweet,
)

articles_bp = Blueprint("articles", __name__)


@dataclass
class Selection:
    """Container for the provider and certification names selected by the user."""

    provider_name: str
    certification_name: str


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


def _percent_encode(value: str) -> str:
    """Return a string percent-encoded according to RFC 3986."""

    return quote(str(value), safe="~-._")


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

    base_url = f"{url_parts.scheme}://{url_parts.netloc}{url_parts.path}"
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


def _publish_tweet(text: str) -> dict:
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

    if oauth1_credentials:
        headers = {
            "Authorization": _build_oauth1_header("POST", X_API_TWEET_URL),
            "Content-Type": "application/json",
        }
    else:
        oauth2_credentials = all((X_API_CLIENT_ID, X_API_CLIENT_SECRET, X_API_REFRESH_TOKEN))

        if oauth2_credentials:
            access_token = _get_x_oauth2_access_token()
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
        else:
            raise RuntimeError(
                "Les identifiants X (Twitter) sont incomplets. Fournissez les clés OAuth 1.0a "
                "(X_API_CONSUMER_KEY, X_API_CONSUMER_SECRET, X_API_ACCESS_TOKEN, "
                "X_API_ACCESS_TOKEN_SECRET) ou un trio OAuth 2.0 (X_API_CLIENT_ID, "
                "X_API_CLIENT_SECRET, X_API_REFRESH_TOKEN)."
            )

    response = requests.post(
        X_API_TWEET_URL,
        headers=headers,
        json={"text": text},
        timeout=30,
    )

    if response.status_code >= 400:
        error_message = response.text
        if response.status_code == 403 and "Unsupported Authentication" in error_message:
            error_message = (
                "L'API X a rejeté l'authentification utilisée. L'envoi de tweets "
                "nécessite désormais des identifiants OAuth 1.0a (user context) ou "
                "un trio OAuth 2.0 user context. Vérifiez la configuration des "
                "variables X_API_CONSUMER_KEY, X_API_CONSUMER_SECRET, "
                "X_API_ACCESS_TOKEN, X_API_ACCESS_TOKEN_SECRET ou fournissez "
                "X_API_CLIENT_ID, X_API_CLIENT_SECRET et X_API_REFRESH_TOKEN."
            )
        raise RuntimeError(
            f"Erreur lors de la publication du tweet ({response.status_code}): {error_message}"
        )

    return response.json()


_X_OAUTH2_TOKEN_CACHE: Optional[Tuple[str, float]] = None


def _get_x_oauth2_access_token(force_refresh: bool = False) -> str:
    """Return a user-context OAuth 2.0 access token for the X API."""

    global _X_OAUTH2_TOKEN_CACHE

    if not force_refresh and _X_OAUTH2_TOKEN_CACHE:
        token, expires_at = _X_OAUTH2_TOKEN_CACHE
        if expires_at > time.time():
            return token

    if not all((X_API_CLIENT_ID, X_API_CLIENT_SECRET, X_API_REFRESH_TOKEN)):
        raise RuntimeError(
            "Impossible de rafraîchir le token OAuth 2.0 : configurez X_API_CLIENT_ID, "
            "X_API_CLIENT_SECRET et X_API_REFRESH_TOKEN."
        )

    basic_credentials = base64.b64encode(
        f"{X_API_CLIENT_ID}:{X_API_CLIENT_SECRET}".encode("utf-8")
    ).decode("utf-8")
    headers = {
        "Authorization": f"Basic {basic_credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": X_API_REFRESH_TOKEN,
    }

    response = requests.post(X_API_TOKEN_URL, headers=headers, data=data, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            "Impossible d'obtenir un token OAuth 2.0 pour X : "
            f"{response.status_code} {response.text}"
        )

    payload = response.json()
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)

    if not access_token:
        raise RuntimeError(
            "La réponse du serveur X ne contient pas de champ access_token : "
            f"{payload}"
        )

    expires_at = time.time() + max(int(expires_in), 0) - 30
    _X_OAUTH2_TOKEN_CACHE = (access_token, expires_at)

    return access_token


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


def _publish_linkedin_post(text: str) -> dict:
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
        payload = {
            "author": LINKEDIN_ORGANIZATION_URN,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
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
        raise RuntimeError(
            f"Erreur lors de la publication LinkedIn ({response.status_code}): {response.text}"
        )

    return response.json()


@articles_bp.route("/")
def index() -> str:
    """Render the article generator interface."""

    return render_template("article_generator.html")


def _extract_selection_payload(data: dict) -> Tuple[int, int, str]:
    """Return the validated identifiers and URL from the request payload."""

    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")
    exam_url = (data.get("exam_url") or "").strip()

    if not provider_id or not certification_id or not exam_url:
        raise ValueError("provider_id, certification_id et exam_url sont requis.")

    try:
        provider_id = int(provider_id)
        certification_id = int(certification_id)
    except (TypeError, ValueError) as exc:  # pragma: no cover - validation only
        raise ValueError("Identifiants invalides.") from exc

    return provider_id, certification_id, exam_url


@articles_bp.route("/generate", methods=["POST"])
def generate_article():
    """Generate the certification article using the OpenAI API."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        article = generate_certification_article(
            selection.certification_name, selection.provider_name, exam_url
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


@articles_bp.route("/run-playbook", methods=["POST"])
def run_playbook():
    """Run the social playbook: publish tweet and LinkedIn post."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_text, tweet_response = _run_tweet_workflow(selection, exam_url)
        linkedin_post, linkedin_response = _run_linkedin_workflow(
            selection, exam_url
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "tweet": tweet_text,
            "tweet_response": tweet_response,
            "linkedin_post": linkedin_post,
            "linkedin_response": linkedin_response,
        }
    )


def _run_tweet_workflow(selection: Selection, exam_url: str) -> Tuple[str, dict]:
    """Generate and publish the certification announcement tweet."""

    tweet_text = generate_certification_tweet(
        selection.certification_name, selection.provider_name, exam_url
    )
    return tweet_text, _publish_tweet(tweet_text)


def _run_linkedin_workflow(selection: Selection, exam_url: str) -> Tuple[str, dict]:
    """Generate and publish the LinkedIn announcement post."""

    linkedin_post = generate_certification_linkedin_post(
        selection.certification_name, selection.provider_name, exam_url
    )
    linkedin_response = _publish_linkedin_post(linkedin_post)
    return linkedin_post, linkedin_response


@articles_bp.route("/generate-tweet", methods=["POST"])
def generate_tweet():
    """Generate the tweet content without publishing it."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_text = generate_certification_tweet(
            selection.certification_name, selection.provider_name, exam_url
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"tweet": tweet_text})


@articles_bp.route("/generate-linkedin", methods=["POST"])
def generate_linkedin():
    """Generate the LinkedIn post content for the selected certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        linkedin_post = generate_certification_linkedin_post(
            selection.certification_name, selection.provider_name, exam_url
        )
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"linkedin_post": linkedin_post})


@articles_bp.route("/publish-tweet", methods=["POST"])
def publish_tweet():
    """Generate and publish the announcement tweet."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_text, tweet_response = _run_tweet_workflow(selection, exam_url)
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"tweet": tweet_text, "tweet_response": tweet_response})


@articles_bp.route("/publish-linkedin", methods=["POST"])
def publish_linkedin():
    """Generate and publish the LinkedIn announcement post."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        linkedin_post, linkedin_response = _run_linkedin_workflow(selection, exam_url)
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {"linkedin_post": linkedin_post, "linkedin_response": linkedin_response}
    )
