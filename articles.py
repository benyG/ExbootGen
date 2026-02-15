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
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple
from urllib.parse import ParseResult, parse_qsl, quote, urlparse

import fitz
import mysql.connector
import requests
from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from config import (
    DB_CONFIG,
    EXAMBOOT_API_KEY,
    EXAMBOOT_CREATE_TEST_URL,
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
    generate_carousel_linkedin_post,
    generate_carousel_topic_ideas,
    generate_linkedin_carousel,
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
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CAROUSEL_TEMPLATE_PATH = BASE_DIR / "docs" / "Caroussel-Template-ExamBoot.pdf"
CAROUSEL_FRAME_X_PADDING_RATIO = 0.07
CAROUSEL_HEADLINE_Y_START_RATIO = 0.23
CAROUSEL_HEADLINE_Y_END_RATIO = 0.59
CAROUSEL_SUBTEXT_Y_START_RATIO = 0.59
CAROUSEL_SUBTEXT_Y_END_RATIO = 0.69
CAROUSEL_KEY_MESSAGE_Y_OFFSET_RATIO = 0.025
CAROUSEL_KEY_MESSAGE_HEIGHT_RATIO = 0.05
CAROUSEL_KEY_MESSAGE_X_INSET_RATIO = 0.2
CAROUSEL_LINE_HEIGHT = 1.12
CAROUSEL_TITLE_COLOR = (0.13, 0.77, 0.37)
CAROUSEL_SUBTEXT_COLOR = (0.22, 0.22, 0.22)
CAROUSEL_CTA_COLOR = (0.22, 0.22, 0.22)
CAROUSEL_FOOTER_WHITE_COLOR = (1.0, 1.0, 1.0)
CAROUSEL_FONT_CANDIDATES = (
    ("Poppins", "Poppins-Regular.ttf", "Poppins-Bold.ttf"),
    ("Montserrat", "Montserrat-Regular.ttf", "Montserrat-Bold.ttf"),
    ("DejaVu Sans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
)
CAROUSEL_FONT_SEARCH_PATHS = (
    BASE_DIR / "fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path("/usr/share/fonts/truetype"),
    Path("/usr/share/fonts/truetype/dejavu"),
)


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


class ExambootTestGenerationError(RuntimeError):
    """Raised when the Examboot test creation API fails."""


def _fetch_carousel_topics(only_available: bool = False) -> list[dict]:
    """Return stored carousel topics ordered from newest to oldest."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    try:
        query = (
            "SELECT id, topic, question_to_address, is_processed, created_at, updated_at "
            "FROM carousel_topics"
        )
        params: tuple = ()
        if only_available:
            query += " WHERE is_processed = 0"
        query += " ORDER BY created_at DESC"
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()


def _get_carousel_topic_by_id(topic_id: int) -> Optional[dict]:
    """Return one carousel topic by identifier."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT id, topic, question_to_address, is_processed FROM carousel_topics WHERE id = %s",
            (topic_id,),
        )
        row = cursor.fetchone()
        return row
    finally:
        cursor.close()
        conn.close()


def _insert_carousel_topic(topic: str, question_to_address: str) -> int:
    """Persist a carousel topic and return its identifier."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO carousel_topics (topic, question_to_address, is_processed, created_at, updated_at)
            VALUES (%s, %s, 0, NOW(), NOW())
            """,
            (topic.strip(), question_to_address.strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        cursor.close()
        conn.close()


def _mark_carousel_topic_processed(topic_id: int) -> None:
    """Set a carousel topic as processed once the PDF is generated."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE carousel_topics SET is_processed = 1, updated_at = NOW() WHERE id = %s",
            (topic_id,),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


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


def _get_existing_course_art_json(certification_id: int) -> Optional[dict]:
    """Return the stored course art JSON for the certification when available."""

    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT art FROM courses WHERE id = %s", (certification_id,))
        row = cursor.fetchone()
    finally:
        cursor.close()
        conn.close()

    if not row or row[0] is None:
        return None

    raw_payload = row[0]
    if isinstance(raw_payload, (bytes, bytearray)):
        raw_payload = raw_payload.decode("utf-8", errors="ignore")

    if isinstance(raw_payload, dict):
        return raw_payload

    if isinstance(raw_payload, str):
        cleaned = raw_payload.strip()
        if not cleaned:
            return None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("La fiche certification enregistrée est invalide.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("La fiche certification enregistrée n'est pas un objet JSON.")
        return parsed

    raise ValueError("Format de fiche certification non supporté dans la base.")


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


def _find_carousel_frame_rect(page: fitz.Page) -> fitz.Rect:
    """Return the bounding rectangle for the carousel text area."""

    drawings = page.get_drawings()
    frame_candidates = []

    def _is_green(color: Optional[Tuple[float, float, float]]) -> bool:
        if not color or len(color) != 3:
            return False
        r, g, b = color
        return g > 0.6 and r < 0.4 and b < 0.4

    for drawing in drawings:
        stroke = drawing.get("color")
        fill = drawing.get("fill")
        rect = drawing.get("rect")
        width = drawing.get("width") or 0
        if rect and stroke and not fill and _is_green(stroke) and width >= 2:
            frame_candidates.append(fitz.Rect(rect))

    if not frame_candidates:
        raise ValueError("Zone de texte introuvable dans le template du carrousel.")

    return max(frame_candidates, key=lambda r: r.get_area())


def _find_font_file(filename: str) -> Optional[Path]:
    for base in CAROUSEL_FONT_SEARCH_PATHS:
        if not base.exists():
            continue
        candidate = base / filename
        if candidate.exists():
            return candidate
        for match in base.rglob(filename):
            return match
    return None


def _resolve_carousel_fonts() -> tuple[str, Optional[Path], str, Optional[Path]]:
    """Return (regular_fontname, regular_fontfile, bold_fontname, bold_fontfile)."""

    for fontname, regular_name, bold_name in CAROUSEL_FONT_CANDIDATES:
        regular_path = _find_font_file(regular_name)
        bold_path = _find_font_file(bold_name)
        if regular_path and bold_path:
            return fontname, regular_path, f"{fontname} Bold", bold_path
        if regular_path:
            return fontname, regular_path, fontname, regular_path

    return "helvetica", None, "helvetica-bold", None


def _fit_font_size(
    text: str,
    target_rect: fitz.Rect,
    fontname: str,
    fontfile: Optional[Path],
    max_size: int,
    min_size: int,
    line_height: float,
    align: int,
) -> int:
    """Return the largest font size that fits within the target rectangle."""

    clean_text = (text or "").strip()
    if not clean_text:
        return max_size

    for size in range(max_size, min_size - 1, -1):
        temp_doc = fitz.open()
        temp_page = temp_doc.new_page(width=target_rect.width, height=target_rect.height)
        test_rect = fitz.Rect(0, 0, target_rect.width, target_rect.height)
        result = temp_page.insert_textbox(
            test_rect,
            clean_text,
            fontsize=size,
            fontname=fontname,
            fontfile=str(fontfile) if fontfile else None,
            align=align,
            lineheight=line_height,
        )
        temp_doc.close()
        if result >= 0:
            return size

    return min_size


def _insert_text_block(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    fontname: str,
    fontfile: Optional[Path],
    max_size: int,
    min_size: int,
    line_height: float,
    align: int,
    color: tuple[float, float, float],
) -> None:
    """Insert text into the page ensuring it fits within the rectangle."""

    clean_text = (text or "").strip()
    if not clean_text:
        return

    font_size = _fit_font_size(
        clean_text,
        rect,
        fontname,
        fontfile,
        max_size,
        min_size,
        line_height,
        align,
    )
    page.insert_textbox(
        rect,
        clean_text,
        fontsize=font_size,
        fontname=fontname,
        fontfile=str(fontfile) if fontfile else None,
        align=align,
        color=color,
        lineheight=line_height,
    )


def _build_carousel_pdf(pages: list[dict]) -> Path:
    """Render the LinkedIn carousel pages into the PDF template."""

    if not CAROUSEL_TEMPLATE_PATH.exists():
        raise FileNotFoundError("Template PDF du carrousel introuvable.")

    template = fitz.open(CAROUSEL_TEMPLATE_PATH)
    output = fitz.open()
    output.insert_pdf(template)

    if template.page_count < 6:
        template.close()
        output.close()
        raise ValueError("Le template du carrousel ne contient pas assez de pages.")

    (
        regular_fontname,
        regular_fontfile,
        bold_fontname,
        bold_fontfile,
    ) = _resolve_carousel_fonts()

    frame_rect = _find_carousel_frame_rect(template.load_page(1))
    page_rect = template.load_page(1).rect

    content_rect = fitz.Rect(
        frame_rect.x0 + frame_rect.width * CAROUSEL_FRAME_X_PADDING_RATIO,
        frame_rect.y0,
        frame_rect.x1 - frame_rect.width * CAROUSEL_FRAME_X_PADDING_RATIO,
        frame_rect.y1,
    )

    headline_rect = fitz.Rect(
        content_rect.x0,
        frame_rect.y0 + frame_rect.height * CAROUSEL_HEADLINE_Y_START_RATIO,
        content_rect.x1,
        frame_rect.y0 + frame_rect.height * CAROUSEL_HEADLINE_Y_END_RATIO,
    )
    subtext_rect = fitz.Rect(
        content_rect.x0,
        frame_rect.y0 + frame_rect.height * CAROUSEL_SUBTEXT_Y_START_RATIO,
        content_rect.x1,
        frame_rect.y0 + frame_rect.height * CAROUSEL_SUBTEXT_Y_END_RATIO,
    )

    key_message_top = frame_rect.y1 - frame_rect.height * CAROUSEL_KEY_MESSAGE_Y_OFFSET_RATIO
    key_message_bottom = key_message_top + frame_rect.height * CAROUSEL_KEY_MESSAGE_HEIGHT_RATIO
    key_message_rect = fitz.Rect(
        frame_rect.x0 + frame_rect.width * CAROUSEL_KEY_MESSAGE_X_INSET_RATIO,
        key_message_top,
        frame_rect.x1 - frame_rect.width * CAROUSEL_KEY_MESSAGE_X_INSET_RATIO,
        min(key_message_bottom, page_rect.y1 - 8),
    )

    for idx, page_payload in enumerate(pages):
        if idx >= output.page_count - 1:
            break
        page = output.load_page(idx)
        footer_color = CAROUSEL_FOOTER_WHITE_COLOR if 1 <= idx <= 4 else CAROUSEL_CTA_COLOR
        _insert_text_block(
            page,
            headline_rect,
            page_payload.get("headline", ""),
            fontname=bold_fontname,
            fontfile=bold_fontfile,
            max_size=52,
            min_size=34,
            line_height=CAROUSEL_LINE_HEIGHT,
            align=1,
            color=CAROUSEL_TITLE_COLOR,
        )
        _insert_text_block(
            page,
            subtext_rect,
            page_payload.get("subtext", ""),
            fontname=regular_fontname,
            fontfile=regular_fontfile,
            max_size=32,
            min_size=20,
            line_height=CAROUSEL_LINE_HEIGHT,
            align=1,
            color=CAROUSEL_SUBTEXT_COLOR,
        )
        _insert_text_block(
            page,
            key_message_rect,
            page_payload.get("key_message", ""),
            fontname=bold_fontname,
            fontfile=bold_fontfile,
            max_size=24,
            min_size=16,
            line_height=CAROUSEL_LINE_HEIGHT,
            align=1,
            color=footer_color,
        )

    filename = f"carousel_{uuid.uuid4().hex}.pdf"
    output_path = UPLOAD_DIR / filename
    output.save(output_path)
    output.close()
    template.close()
    return output_path


def _normalize_carousel_pages(payload: dict) -> list[dict]:
    """Normalize and validate the carousel payload structure."""

    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list) or len(pages) != 5:
        raise ValueError("Le carrousel doit contenir exactement 5 pages.")

    normalized_pages = []
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError("Chaque page du carrousel doit être un objet JSON.")
        normalized_pages.append(
            {
                "headline": str(page.get("headline", "")).strip(),
                "subtext": str(page.get("subtext", "")).strip(),
                "key_message": str(page.get("key_message", "")).strip(),
            }
        )

    return normalized_pages


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

    try:
        with image_path.open("rb") as file_handle:
            response = requests.post(
                X_API_MEDIA_UPLOAD_URL,
                headers=headers,
                files={"media": file_handle},
                timeout=30,
            )
    except requests.exceptions.RequestException as exc:
        raise SocialPublishError(
            "Impossible de se connecter à X (Twitter) pour téléverser l'image: "
            f"{exc}",
            status_code=502,
        ) from exc

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

    try:
        response = requests.post(
            X_API_TWEET_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        raise SocialPublishError(
            "Impossible de se connecter à X (Twitter) pour publier le tweet: "
            f"{exc}",
            status_code=502,
        ) from exc

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


@articles_bp.route("/carousel-topics")
def carousel_topics_page() -> str:
    """Render the carousel topic management interface."""

    return render_template("carousel_topics.html")


@articles_bp.route("/carousel-topics/list")
def carousel_topics_list():
    """Return saved carousel topics."""

    only_available = request.args.get("available") == "1"
    return jsonify({"topics": _fetch_carousel_topics(only_available=only_available)})


@articles_bp.route("/carousel-topics/generate-ideas", methods=["POST"])
def carousel_topics_generate_ideas():
    """Generate 20 AI topic ideas for LinkedIn carousels."""

    try:
        payload = generate_carousel_topic_ideas()
    except Exception as exc:  # pragma: no cover - external API call
        return jsonify({"error": str(exc)}), 500

    topics = payload.get("topics") if isinstance(payload, dict) else None
    if not isinstance(topics, list):
        return jsonify({"error": "Réponse IA invalide."}), 500

    normalized_topics = []
    for item in topics:
        if not isinstance(item, dict):
            continue
        topic = (item.get("topic") or "").strip()
        question_to_address = (item.get("question_to_address") or "").strip()
        if not topic or not question_to_address:
            continue
        normalized_topics.append(
            {
                "topic": topic,
                "question_to_address": question_to_address,
            }
        )

    return jsonify({"topics": normalized_topics})


@articles_bp.route("/carousel-topics/save", methods=["POST"])
def carousel_topics_save():
    """Save one or many carousel topics into the database."""

    data = request.get_json() or {}
    raw_topics = data.get("topics")
    if not isinstance(raw_topics, list) or not raw_topics:
        return jsonify({"error": "Le champ topics est requis et doit être une liste."}), 400

    inserted = 0
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        topic = (item.get("topic") or "").strip()
        question_to_address = (item.get("question_to_address") or "").strip()
        if not topic or not question_to_address:
            continue
        _insert_carousel_topic(topic, question_to_address)
        inserted += 1

    if inserted == 0:
        return jsonify({"error": "Aucun sujet valide à enregistrer."}), 400

    return jsonify({"saved": inserted})


def _extract_selection_payload(data: dict) -> Tuple[int, int, str, str]:
    """Return the validated identifiers and URL from the request payload."""

    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")
    exam_url = (data.get("exam_url") or "").strip()
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


def _extract_provider_certification(data: dict) -> Tuple[int, int]:
    """Validate the provider and certification identifiers from the payload."""

    provider_id = data.get("provider_id")
    certification_id = data.get("certification_id")

    if not provider_id or not certification_id:
        raise ValueError("provider_id et certification_id sont requis.")

    try:
        provider_id = int(provider_id)
        certification_id = int(certification_id)
    except (TypeError, ValueError) as exc:  # pragma: no cover - validation only
        raise ValueError("Identifiants invalides.") from exc

    return provider_id, certification_id


def ensure_exam_url(certification_id: int, exam_url: str) -> Tuple[str, bool]:
    """Return a usable Examboot link, generating it when missing.

    Returns a tuple of (url, generated) so callers can update state or logs
    when a new link was created.
    """

    cleaned = (exam_url or "").strip()
    if cleaned:
        return cleaned, False

    return _create_shareable_examboot_test(certification_id), True


def _create_shareable_examboot_test(certification_id: int) -> str:
    """Create a shareable Examboot test and return the resulting URL."""

    if not EXAMBOOT_API_KEY:
        raise ExambootTestGenerationError(
            "La clé API Examboot est manquante. Configurez la variable d'environnement API_KEY."
        )

    payload = {
        "type": "shareable",
        "quest": 10,
        "certi": certification_id,
        "timer": 20,
    }
    headers = {
        "Authorization": f"Bearer {EXAMBOOT_API_KEY}",
        "x-api-key": EXAMBOOT_API_KEY,
    }

    try:
        response = requests.post(
            EXAMBOOT_CREATE_TEST_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:  # pragma: no cover - network errors
        raise ExambootTestGenerationError("Impossible de contacter l'API Examboot.") from exc

    if response.status_code >= 400:
        raise ExambootTestGenerationError(
            f"Erreur lors de la génération du test Examboot ({response.status_code})."
        )

    try:
        data = response.json()
    except ValueError as exc:  # pragma: no cover - invalid JSON
        raise ExambootTestGenerationError(
            "Réponse invalide reçue depuis l'API Examboot."
        ) from exc

    url = data.get("url") if isinstance(data, dict) else None
    if not url:
        raise ExambootTestGenerationError(
            "La réponse de l'API Examboot ne contient pas d'URL de test."
        )

    return url


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

    try:
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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
            "exam_url": exam_url,
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

    try:
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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
            "exam_url": exam_url,
        }
    )


@articles_bp.route("/generate-exam-test", methods=["POST"])
def generate_exam_test():
    """Generate a shareable Examboot test for the selected certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id = _extract_provider_certification(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        _fetch_selection(provider_id, certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        url = _create_shareable_examboot_test(certification_id)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify({"url": url})


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

    try:
        exam_url, generated_link = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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

    exam_error: Optional[str] = None
    exam_auto_generated = generated_link
    exam_generated = True

    article_payload: Optional[dict] = None
    article_error: Optional[str] = None
    tweet_text: str = ""
    tweet_result: SocialPostResult = SocialPostResult(text="")
    linkedin_text: str = ""
    linkedin_result: SocialPostResult = SocialPostResult(text="")
    course_art_payload: Optional[dict] = None

    with ThreadPoolExecutor(max_workers=4) as executor:
        article_future = executor.submit(
            _generate_and_persist_article_task,
            selection,
            exam_url,
            topic_type,
            certification_id,
        )
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

        try:
            article_payload = article_future.result()
        except Exception as exc:  # pragma: no cover - surfaced in response
            article_error = str(exc)

        try:
            tweet_text, tweet_result = tweet_future.result()
        except Exception as exc:  # pragma: no cover - surfaced in response
            tweet_result = SocialPostResult(
                text="",
                published=False,
                status_code=500,
                error=str(exc),
            )

        try:
            linkedin_text, linkedin_result = linkedin_future.result()
        except Exception as exc:  # pragma: no cover - surfaced in response
            linkedin_result = SocialPostResult(
                text="",
                published=False,
                status_code=500,
                error=str(exc),
            )

        if course_art_future:
            try:
                course_art_payload = course_art_future.result()
            except Exception as exc:  # pragma: no cover - surfaced in response
                course_art_payload = {"course_art": None, "error": str(exc)}

    response_payload = {
        "article": article_payload["article"] if article_payload else "",
        "blog_id": article_payload["blog_id"] if article_payload else None,
        "title": article_payload["title"] if article_payload else None,
        "summary": article_payload["summary"] if article_payload else None,
        "provider_name": selection.provider_name,
        "certification_name": selection.certification_name,
        "exam_url": exam_url,
        "exam_generated": exam_generated,
        "exam_auto_generated": exam_auto_generated,
        "exam_error": exam_error,
        "tweet": tweet_text,
        "tweet_response": tweet_result.response,
        "tweet_published": tweet_result.published,
        "tweet_status_code": tweet_result.status_code,
        "linkedin_post": linkedin_text,
        "linkedin_response": linkedin_result.response,
        "linkedin_published": linkedin_result.published,
        "linkedin_status_code": linkedin_result.status_code,
    }
    if article_error:
        response_payload["article_error"] = article_error
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

    playbook_steps = []
    playbook_steps.append(
        {
            "id": "exam",
            "label": "Test Examboot",
            "success": exam_generated,
            "message": (
                "Test Examboot généré automatiquement"
                if exam_auto_generated
                else "Lien Examboot existant utilisé"
            ),
        }
    )

    if article_payload:
        article_message = (
            f"Article enregistré (#{article_payload['blog_id']})"
            if article_payload.get("blog_id")
            else "Article généré"
        )
        playbook_steps.append(
            {
                "id": "article",
                "label": "Article",
                "success": True,
                "message": article_message,
            }
        )
    else:
        playbook_steps.append(
            {
                "id": "article",
                "label": "Article",
                "success": False,
                "message": (
                    f"Article non généré : {article_error}"
                    if article_error
                    else "Article non généré"
                ),
            }
        )

    playbook_steps.append(
        {
            "id": "tweet",
            "label": "Tweet",
            "success": bool(tweet_result.published),
            "message": (
                "Tweet publié"
                if tweet_result.published
                else (
                    f"Tweet non publié : {tweet_result.error}"
                    if tweet_result.error
                    else "Tweet non publié"
                )
            ),
        }
    )

    playbook_steps.append(
        {
            "id": "linkedin",
            "label": "LinkedIn",
            "success": bool(linkedin_result.published),
            "message": (
                "LinkedIn publié"
                if linkedin_result.published
                else (
                    f"LinkedIn non publié : {linkedin_result.error}"
                    if linkedin_result.error
                    else "LinkedIn non publié"
                )
            ),
        }
    )

    if topic_type == COURSE_ART_TOPIC:
        if course_art_payload and course_art_payload.get("course_art") is not None:
            playbook_steps.append(
                {
                    "id": "course_art",
                    "label": "Fiche certification",
                    "success": True,
                    "message": "Fiche certification enregistrée",
                }
            )
        else:
            course_art_error = None
            if course_art_payload:
                course_art_error = course_art_payload.get("error")
            playbook_steps.append(
                {
                    "id": "course_art",
                    "label": "Fiche certification",
                    "success": False,
                    "message": (
                        f"Fiche non enregistrée : {course_art_error}"
                        if course_art_error
                        else "Fiche certification non enregistrée"
                    ),
                }
            )

    response_payload["playbook_steps"] = playbook_steps

    return jsonify(response_payload)


def run_scheduled_publication(
    provider_id: int,
    certification_id: int,
    exam_url: str,
    topic_type: str,
    channels: Iterable[str],
    attach_image: bool = False,
) -> dict:
    """Execute the publication workflow for scheduled posts.

    This function mirrors the Article Builder playbook but only runs the tasks
    requested by ``channels`` (e.g. ``["linkedin", "x", "article"]``). It
    returns the same payload structure used by the UI so that callers can log
    or persist outcomes.
    """

    allowed_channels = {"article", "linkedin", "x"}
    channels_set = {channel for channel in channels if channel in allowed_channels}
    if not channels_set:
        invalid = ", ".join({channel for channel in channels if channel}) or "aucun canal"  # type: ignore[arg-type]
        raise ValueError(
            f"Aucun canal sélectionné pour la publication (canaux reçus : {invalid})."
        )

    exam_url, _ = ensure_exam_url(certification_id, exam_url)

    selection = _fetch_selection(provider_id, certification_id)

    article_payload: Optional[dict] = None
    article_error: Optional[str] = None
    tweet_text: str = ""
    tweet_result: Optional[SocialPostResult] = None
    linkedin_text: str = ""
    linkedin_result: Optional[SocialPostResult] = None
    course_art_payload: Optional[dict] = None

    with ThreadPoolExecutor(max_workers=4) as executor:
        article_future = (
            executor.submit(
                _generate_and_persist_article_task,
                selection,
                exam_url,
                topic_type,
                certification_id,
            )
            if "article" in channels_set
            else None
        )
        tweet_future = (
            executor.submit(
                _generate_and_publish_tweet_task,
                selection,
                exam_url,
                topic_type,
                attach_image,
            )
            if "x" in channels_set
            else None
        )
        linkedin_future = (
            executor.submit(
                _generate_and_publish_linkedin_task,
                selection,
                exam_url,
                topic_type,
                attach_image,
            )
            if "linkedin" in channels_set
            else None
        )
        course_art_future = (
            executor.submit(
                _generate_and_store_course_art_task,
                selection,
                certification_id,
            )
            if topic_type == COURSE_ART_TOPIC and "article" in channels_set
            else None
        )

        if article_future:
            try:
                article_payload = article_future.result()
            except Exception as exc:  # pragma: no cover - surfaced to caller
                article_error = str(exc)

        if tweet_future:
            try:
                tweet_text, tweet_result = tweet_future.result()
            except Exception as exc:  # pragma: no cover - surfaced to caller
                tweet_result = SocialPostResult(
                    text="",
                    published=False,
                    status_code=500,
                    error=str(exc),
                )

        if linkedin_future:
            try:
                linkedin_text, linkedin_result = linkedin_future.result()
            except Exception as exc:  # pragma: no cover - surfaced to caller
                linkedin_result = SocialPostResult(
                    text="",
                    published=False,
                    status_code=500,
                    error=str(exc),
                )

        if course_art_future:
            try:
                course_art_payload = course_art_future.result()
            except Exception as exc:  # pragma: no cover - surfaced to caller
                course_art_payload = {"course_art": None, "error": str(exc)}

    payload: dict = {
        "article": article_payload["article"] if article_payload else "",
        "blog_id": article_payload["blog_id"] if article_payload else None,
        "title": article_payload["title"] if article_payload else None,
        "summary": article_payload["summary"] if article_payload else None,
        "provider_name": selection.provider_name,
        "certification_name": selection.certification_name,
        "exam_url": exam_url,
        "tweet": tweet_text,
        "tweet_result": tweet_result,
        "linkedin_post": linkedin_text,
        "linkedin_result": linkedin_result,
    }
    if article_error:
        payload["article_error"] = article_error
    if course_art_payload:
        payload["course_art"] = course_art_payload.get("course_art")
        if course_art_payload.get("error"):
            payload["course_art_error"] = course_art_payload["error"]
    return payload


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

    try:
        tweet_text = generate_certification_tweet(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    except Exception as exc:
        return "", SocialPostResult(
            text="",
            published=False,
            status_code=500,
            error=str(exc),
        )

    return tweet_text, _retry_social_publish_once(
        workflow=_run_tweet_workflow,
        selection=selection,
        exam_url=exam_url,
        topic_type=topic_type,
        attach_image=attach_image,
        text=tweet_text,
        channel_label="Tweet",
        text_kwarg="tweet_text",
    )


def _generate_and_publish_linkedin_task(
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool,
) -> Tuple[str, SocialPostResult]:
    """Generate the LinkedIn post content and trigger its publication."""

    try:
        linkedin_post = generate_certification_linkedin_post(
            selection.certification_name,
            selection.provider_name,
            exam_url,
            topic_type,
        )
    except Exception as exc:
        return "", SocialPostResult(
            text="",
            published=False,
            status_code=500,
            error=str(exc),
        )

    return linkedin_post, _retry_social_publish_once(
        workflow=_run_linkedin_workflow,
        selection=selection,
        exam_url=exam_url,
        topic_type=topic_type,
        attach_image=attach_image,
        text=linkedin_post,
        channel_label="LinkedIn",
        text_kwarg="linkedin_post",
    )


def _retry_social_publish_once(
    *,
    workflow,
    selection: Selection,
    exam_url: str,
    topic_type: str,
    attach_image: bool,
    text: str,
    channel_label: str,
    text_kwarg: str,
) -> SocialPostResult:
    """Try one social publish operation, then retry exactly once on failure."""

    workflow_args = {
        "selection": selection,
        "exam_url": exam_url,
        "topic_type": topic_type,
        "attach_image": attach_image,
    }
    workflow_args[text_kwarg] = text

    first_result: Optional[SocialPostResult] = None
    for attempt in range(2):
        try:
            result = workflow(**workflow_args)
        except Exception as exc:  # pragma: no cover - defensive fallback
            result = SocialPostResult(
                text=text,
                published=False,
                status_code=500,
                error=str(exc),
            )

        if result.published:
            return result

        first_result = first_result or result

    error_parts = []
    if first_result and first_result.error:
        error_parts.append(f"Tentative 1: {first_result.error}")
    if first_result and first_result.media_filename:
        media_filename = first_result.media_filename
    else:
        media_filename = None
    final_error = (
        f"{channel_label} non publié après 2 tentatives. "
        + " ".join(error_parts)
    ).strip()
    return SocialPostResult(
        text=text,
        published=False,
        status_code=(first_result.status_code if first_result else 500),
        error=final_error,
        media_filename=media_filename,
    )


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


def run_scheduled_carousel_publication(
    provider_id: int,
    certification_id: int,
    exam_url: str,
    topic_id: int,
    attach_image: bool = False,
) -> dict:
    """Generate a carousel PDF from a saved topic and publish its companion post on LinkedIn."""

    topic = _get_carousel_topic_by_id(topic_id)
    if not topic:
        raise ValueError("Sujet de carrousel introuvable.")
    if int(topic.get("is_processed") or 0) == 1:
        raise ValueError("Ce sujet de carrousel a déjà été traité.")

    subject = (topic.get("topic") or "").strip()
    question = (topic.get("question_to_address") or "").strip()
    if not subject or not question:
        raise ValueError("Le sujet sélectionné est invalide.")

    exam_url, _ = ensure_exam_url(certification_id, exam_url)
    selection = _fetch_selection(provider_id, certification_id)

    carousel_payload = generate_linkedin_carousel(subject, question)
    pages = _normalize_carousel_pages(carousel_payload)
    pdf_path = _build_carousel_pdf(pages)

    _mark_carousel_topic_processed(topic_id)

    linkedin_text = generate_carousel_linkedin_post(subject, question, exam_url)
    linkedin_result = _run_linkedin_workflow(
        selection,
        exam_url,
        "preparation_methodology",
        attach_image=attach_image,
        linkedin_post=linkedin_text,
    )

    return {
        "carousel": carousel_payload,
        "pdf_filename": pdf_path.name,
        "pdf_url": url_for("articles.download_carousel", filename=pdf_path.name),
        "linkedin_post": linkedin_text,
        "linkedin_result": linkedin_result,
        "topic_id": topic_id,
        "topic": subject,
    }


@articles_bp.route("/generate-tweet", methods=["POST"])
def generate_tweet():
    """Generate the tweet content without publishing it."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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

    return jsonify({"tweet": tweet_text, "exam_url": exam_url})


@articles_bp.route("/generate-linkedin", methods=["POST"])
def generate_linkedin():
    """Generate the LinkedIn post content for the selected certification."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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

    return jsonify({"linkedin_post": linkedin_post, "exam_url": exam_url})


@articles_bp.route("/generate-carousel", methods=["POST"])
def generate_carousel():
    """Generate a LinkedIn carousel PDF based on a subject and question."""

    data = request.get_json() or {}
    subject = (data.get("subject") or "").strip()
    question = (data.get("question") or "").strip()
    topic_id = data.get("topic_id")

    if not subject or not question:
        return jsonify({"error": "Le sujet et la question sont requis."}), 400

    topic_id_int = None
    if topic_id not in (None, ""):
        try:
            topic_id_int = int(topic_id)
        except (TypeError, ValueError):
            return jsonify({"error": "topic_id invalide."}), 400

    try:
        carousel_payload = generate_linkedin_carousel(subject, question)
        pages = _normalize_carousel_pages(carousel_payload)
        pdf_path = _build_carousel_pdf(pages)
        if topic_id_int is not None:
            _mark_carousel_topic_processed(topic_id_int)
    except Exception as exc:  # pragma: no cover - external API issues
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "carousel": carousel_payload,
            "pdf_url": url_for("articles.download_carousel", filename=pdf_path.name),
        }
    )


@articles_bp.route("/carousel/<path:filename>")
def download_carousel(filename: str):
    """Download the generated carousel PDF."""

    try:
        resolved_path = (UPLOAD_DIR / filename).resolve()
        resolved_path.relative_to(UPLOAD_DIR.resolve())
    except (ValueError, RuntimeError):
        return jsonify({"error": "Chemin de fichier invalide."}), 400

    file_path = resolved_path
    if file_path.suffix.lower() != ".pdf":
        return jsonify({"error": "Format de fichier invalide."}), 400
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "Fichier introuvable."}), 404

    return send_file(
        file_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=file_path.name,
    )


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


@articles_bp.route("/existing-course-art")
def get_existing_course_art():
    """Return the existing course art JSON for the current certification."""

    try:
        provider_id, certification_id = _extract_provider_certification(request.args)
        _fetch_selection(provider_id, certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        course_art = _get_existing_course_art_json(certification_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 500
    except mysql.connector.Error as exc:
        return jsonify({"error": f"Erreur lors de la lecture de la fiche: {exc}"}), 500

    return jsonify({"course_art": course_art})


@articles_bp.route("/api/mcp/certifications/<int:cert_id>/course-art", methods=["POST"])
def mcp_publish_course_art(cert_id: int):
    """Generate and persist the certification presentation sheet for MCP."""

    data = request.get_json() or {}
    provider_id = data.get("provider_id")
    if not provider_id:
        return jsonify({"error": "provider_id requis."}), 400

    try:
        provider_id = int(provider_id)
    except (TypeError, ValueError):
        return jsonify({"error": "provider_id invalide."}), 400

    try:
        selection = _fetch_selection(provider_id, cert_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404

    try:
        course_art = generate_certification_course_art(
            selection.certification_name,
            selection.provider_name,
        )
        _save_course_art_json(cert_id, course_art)
    except Exception as exc:  # pragma: no cover - external dependencies
        return jsonify({"error": str(exc)}), 500

    return jsonify(
        {
            "course_art": course_art,
            "provider_name": selection.provider_name,
            "certification_name": selection.certification_name,
            "topic_type": COURSE_ART_TOPIC,
        }
    )


@articles_bp.route("/publish-tweet", methods=["POST"])
def publish_tweet():
    """Generate and publish the announcement tweet."""

    data = request.get_json() or {}

    try:
        provider_id, certification_id, exam_url, topic_type = _extract_selection_payload(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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
        "exam_url": exam_url,
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
        exam_url, _ = ensure_exam_url(certification_id, exam_url)
    except ExambootTestGenerationError as exc:
        return jsonify({"error": str(exc)}), 502

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
        "exam_url": exam_url,
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
