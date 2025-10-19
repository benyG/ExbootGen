"""Blueprint implementing the certification article generator workflow."""

from __future__ import annotations

from dataclasses import dataclass

import mysql.connector
import requests
from flask import Blueprint, jsonify, render_template, request

from config import DB_CONFIG, X_API_BEARER_TOKEN, X_API_TWEET_URL
from openai_api import generate_certification_article, generate_certification_tweet

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


def _publish_tweet(text: str) -> dict:
    """Publish a tweet using the X (Twitter) v2 API."""

    if not X_API_BEARER_TOKEN:
        raise RuntimeError("X_API_BEARER_TOKEN n'est pas configuré.")

    tweet_url = X_API_TWEET_URL or "https://api.x.com/2/tweets"
    headers = {
        "Authorization": f"Bearer {X_API_BEARER_TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.post(tweet_url, headers=headers, json={"text": text}, timeout=30)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Erreur lors de la publication du tweet ({response.status_code}): {response.text}"
        )
    return response.json()


@articles_bp.route("/")
def index() -> str:
    """Render the article generator interface."""

    return render_template("article_generator.html")


@articles_bp.route("/generate", methods=["POST"])
def generate_article():
    """Generate the certification article using the OpenAI API."""

    data = request.get_json() or {}
    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")
    exam_url = (data.get("exam_url") or "").strip()

    if not provider_id or not certification_id or not exam_url:
        return (
            jsonify({"error": "provider_id, certification_id et exam_url sont requis."}),
            400,
        )

    try:
        provider_id = int(provider_id)
        certification_id = int(certification_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifiants invalides."}), 400

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
    """Generate and publish the announcement tweet for the selected certification."""

    data = request.get_json() or {}
    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")
    exam_url = (data.get("exam_url") or "").strip()

    if not provider_id or not certification_id or not exam_url:
        return (
            jsonify({"error": "provider_id, certification_id et exam_url sont requis."}),
            400,
        )

    try:
        provider_id = int(provider_id)
        certification_id = int(certification_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Identifiants invalides."}), 400

    try:
        selection = _fetch_selection(provider_id, certification_id)
        tweet_text = generate_certification_tweet(
            selection.certification_name, selection.provider_name, exam_url
        )
        tweet_response = _publish_tweet(tweet_text)
    except Exception as exc:  # pragma: no cover - propagated to client for visibility
        return jsonify({"error": str(exc)}), 500

    return jsonify({"tweet": tweet_text, "response": tweet_response})
