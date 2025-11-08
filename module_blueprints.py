"""Blueprint and API endpoints to manage module blueprints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import mysql.connector
from flask import Blueprint, jsonify, render_template, request

from config import DB_CONFIG
from openai_api import generate_module_blueprint_excerpt

module_blueprints_bp = Blueprint("module_blueprints", __name__)


def _fetchall(query: str, params: Iterable | None = None) -> list[dict]:
    """Execute a SELECT query and return rows as dictionaries."""

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(query, params or ())
            rows = cur.fetchall()
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
        "SELECT id, name FROM courses WHERE prov = %s ORDER BY name",
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


@module_blueprints_bp.route("/api/modules/<int:module_id>", methods=["PATCH"])
def api_update_module(module_id: int):
    payload = request.get_json(silent=True) or {}
    blueprint_text = payload.get("blueprint")

    if blueprint_text is None:
        value = None
    else:
        value = blueprint_text.strip() if isinstance(blueprint_text, str) else str(blueprint_text)
        if not value:
            value = None

    conn = mysql.connector.connect(**DB_CONFIG)
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE modules SET blueprint = %s WHERE id = %s",
                (value, module_id),
            )
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "Module introuvable."}), 404
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
            cur.execute("SELECT name FROM courses WHERE id = %s", (cert_id,))
            cert_row = cur.fetchone()
            if not cert_row:
                return jsonify({"error": "Certification introuvable."}), 404

            cert_name = cert_row["name"]

            cur.execute(
                "SELECT id, name, blueprint FROM modules WHERE course = %s ORDER BY name",
                (cert_id,),
            )
            modules = cur.fetchall()
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
