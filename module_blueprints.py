"""Blueprint and API endpoints to manage module blueprints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import mysql.connector
from flask import Blueprint, jsonify, render_template, request

from config import DB_CONFIG
from openai_api import generate_module_blueprint_excerpt

module_blueprints_bp = Blueprint("module_blueprints", __name__)


def _to_text(value):
    """Return ``value`` decoded to a ``str`` when it is binary data."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _decode_row(row: dict) -> dict:
    """Return a copy of ``row`` with every binary field decoded to text."""

    return {key: _to_text(value) for key, value in row.items()}


def _fetchall(query: str, params: Iterable | None = None) -> list[dict]:
    """Execute a SELECT query and return rows as dictionaries."""

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(query, params or ())
            rows = cur.fetchall()
            if rows and isinstance(rows[0], dict):
                rows = [_decode_row(row) for row in rows]
        finally:
            cur.close()
    finally:
        conn.close()
    return rows


@module_blueprints_bp.route("/")
def index() -> str:
    """Render the interactive UI used to manage module blueprints."""

    return render_template("blueprint.html")


@module_blueprints_bp.route("/api/providers")
def api_providers():
    rows = _fetchall("SELECT id, name FROM provs ORDER BY name")
    return jsonify(rows)


@module_blueprints_bp.route("/api/certifications/<int:prov_id>")
def api_certifications(prov_id: int):
    rows = _fetchall(
        "SELECT id, name, code_cert_key AS code_cert FROM courses WHERE prov = %s ORDER BY name",
        (prov_id,),
    )
    return jsonify(rows)


@module_blueprints_bp.route("/api/certifications/<int:cert_id>/modules")
def api_modules(cert_id: int):
    rows = _fetchall(
        "SELECT id, name, descr, blueprint FROM modules WHERE course = %s ORDER BY name",
        (cert_id,),
    )
    return jsonify(rows)


def _normalise_blueprint(value) -> str | None:
    """Return a trimmed blueprint string or None."""

    if value is None:
        return None
    value = _to_text(value)
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


@module_blueprints_bp.route("/api/modules/<int:module_id>", methods=["PATCH"])
def api_update_module(module_id: int):
    payload = request.get_json(silent=True) or {}
    value = _normalise_blueprint(payload.get("blueprint"))

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE modules SET blueprint = %s WHERE id = %s",
                (value, module_id),
            )
            if cur.rowcount == 0:
                cur.execute("SELECT 1 FROM modules WHERE id = %s", (module_id,))
                exists = cur.fetchone()
                if not exists:
                    conn.rollback()
                    return jsonify({"error": "Module introuvable."}), 404
                conn.commit()
                return jsonify({"status": "unchanged", "module_id": module_id})
            conn.commit()
        except mysql.connector.Error as exc:
            conn.rollback()
            return jsonify({"error": str(exc)}), 500
        finally:
            cur.close()
    finally:
        conn.close()

    return jsonify({"status": "updated", "module_id": module_id})


@module_blueprints_bp.route(
    "/api/certifications/<int:cert_id>/modules", methods=["PATCH"]
)
def api_bulk_update_modules(cert_id: int):
    payload = request.get_json(silent=True) or {}
    modules = payload.get("modules")

    if not isinstance(modules, list):
        return jsonify({"error": "Le corps de la requête doit contenir une liste 'modules'."}), 400

    to_update: dict[int, str | None] = {}
    for item in modules:
        if not isinstance(item, dict):
            continue
        module_id = item.get("id")
        try:
            module_id = int(module_id)
        except (TypeError, ValueError):
            continue
        to_update[module_id] = _normalise_blueprint(item.get("blueprint"))

    if not to_update:
        return jsonify({"error": "Aucun module valide à mettre à jour."}), 400

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT 1 FROM courses WHERE id = %s", (cert_id,))
            if not cur.fetchone():
                return jsonify({"error": "Certification introuvable."}), 404

            placeholders = ",".join(["%s"] * len(to_update))
            query = (
                "SELECT id, name, blueprint FROM modules "
                "WHERE course = %s AND id IN (" + placeholders + ")"
            )
            params = (cert_id, *to_update.keys())
            cur.execute(query, params)
            rows = cur.fetchall()
            rows = [_decode_row(row) for row in rows]
        finally:
            cur.close()

        existing = {row["id"]: row for row in rows}
        results: list[dict] = []
        updates: list[tuple[str | None, int]] = []

        for module_id, value in to_update.items():
            module = existing.get(module_id)
            if not module:
                results.append(
                    {
                        "module_id": module_id,
                        "module_name": None,
                        "status": "not_found",
                        "message": "Module introuvable pour cette certification.",
                    }
                )
                continue

            current = _normalise_blueprint(module.get("blueprint"))
            if current == value:
                results.append(
                    {
                        "module_id": module_id,
                        "module_name": module.get("name"),
                        "status": "unchanged",
                    }
                )
                continue

            updates.append((value, module_id))
            results.append(
                {
                    "module_id": module_id,
                    "module_name": module.get("name"),
                    "status": "updated",
                }
            )

        if updates:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE modules SET blueprint = %s WHERE id = %s",
                    updates,
                )
                conn.commit()
            except mysql.connector.Error as exc:
                conn.rollback()
                return jsonify({"error": str(exc)}), 500
            finally:
                cur.close()

        updated_count = sum(1 for item in results if item.get("status") == "updated")
        unchanged_count = sum(1 for item in results if item.get("status") == "unchanged")
        not_found_count = sum(1 for item in results if item.get("status") == "not_found")
    finally:
        conn.close()

    return jsonify(
        {
            "certification_id": cert_id,
            "requested": len(modules),
            "processed": len(results),
            "updated": updated_count,
            "unchanged": unchanged_count,
            "not_found": not_found_count,
            "results": results,
        }
    )


@module_blueprints_bp.route(
    "/api/certifications/<int:cert_id>/generate-blueprints", methods=["POST"]
)
def api_generate_blueprints(cert_id: int):
    payload = request.get_json(silent=True) or {}
    mode = (payload.get("mode") or "missing").lower()
    if mode not in {"all", "missing"}:
        mode = "missing"
    module_ids = payload.get("module_ids")

    if module_ids is not None:
        if not isinstance(module_ids, (list, tuple)):
            return jsonify({"error": "module_ids doit être une liste."}), 400
        try:
            requested_ids = {int(mid) for mid in module_ids}
        except (TypeError, ValueError):
            return jsonify({"error": "module_ids doit contenir uniquement des entiers."}), 400
    else:
        requested_ids = None

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT name, code_cert_key AS code_cert FROM courses WHERE id = %s",
                (cert_id,),
            )
            cert_row = cur.fetchone()
            if not cert_row:
                return jsonify({"error": "Certification introuvable."}), 404

            cert_name = cert_row["name"]
            cert_code = (cert_row.get("code_cert") or "").strip()

            cur.execute(
                "SELECT id, name, blueprint FROM modules WHERE course = %s ORDER BY name",
                (cert_id,),
            )
            modules = cur.fetchall()
            modules = [_decode_row(row) for row in modules]
        finally:
            cur.close()
    finally:
        conn.close()

    if not modules:
        return jsonify({"error": "Aucun domaine enregistré pour cette certification."}), 404

    if requested_ids is not None:
        targets = [module for module in modules if module["id"] in requested_ids]
    else:
        targets = list(modules)

    if mode != "all":
        targets = [m for m in targets if not (m.get("blueprint") or "").strip()]

    minimum_parallel = min(4, len(modules)) if modules else 0
    if minimum_parallel and len(targets) < minimum_parallel:
        existing = {module["id"] for module in targets}
        for module in modules:
            if module["id"] in existing:
                continue
            targets.append(module)
            existing.add(module["id"])
            if len(targets) >= minimum_parallel:
                break

    if not targets:
        return jsonify(
            {
                "certification": cert_name,
                "total_modules": len(modules),
                "processed": 0,
                "updated": 0,
                "results": [],
                "message": "Aucun module à traiter.",
            }
        )

    max_workers = max(4, min(8, len(targets)))
    results: list[dict] = []
    updates: list[tuple[str, int]] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                generate_module_blueprint_excerpt,
                cert_name,
                module["name"],
                cert_code,
            ): module
            for module in targets
        }

        for future in as_completed(future_map):
            module = future_map[future]
            module_id = module["id"]
            module_name = module["name"]
            try:
                content = (future.result() or "").strip()
            except Exception as exc:  # pragma: no cover - network failures
                results.append(
                    {
                        "module_id": module_id,
                        "module_name": module_name,
                        "status": "error",
                        "error": str(exc),
                    }
                )
                continue

            updates.append((content, module_id))
            results.append(
                {
                    "module_id": module_id,
                    "module_name": module_name,
                    "status": "success",
                    "blueprint": content,
                }
            )

    results.sort(key=lambda item: item["module_name"].lower())

    if updates:
        conn = mysql.connector.connect(**DB_CONFIG)
        try:
            cur = conn.cursor()
            try:
                cur.executemany(
                    "UPDATE modules SET blueprint = %s WHERE id = %s",
                    updates,
                )
                conn.commit()
            except mysql.connector.Error as exc:
                conn.rollback()
                return jsonify({"error": str(exc)}), 500
            finally:
                cur.close()
        finally:
            conn.close()

    return jsonify(
        {
            "certification": cert_name,
            "mode": mode,
            "total_modules": len(modules),
            "processed": len(targets),
            "updated": len(updates),
            "results": results,
        }
    )


@module_blueprints_bp.route(
    "/api/mcp/certifications/<int:cert_id>/generate-blueprints", methods=["POST"]
)
def api_generate_blueprints_mcp(cert_id: int):
    """MCP wrapper for blueprint generation."""

    return api_generate_blueprints(cert_id)
