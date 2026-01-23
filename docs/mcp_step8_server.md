# Étape 8 — Serveur MCP (tools)

Cette étape expose un serveur MCP minimal qui annonce les tools disponibles et permet de les invoquer.

## Endpoints

- `GET /api/mcp/tools`
  - Retourne la liste des tools MCP exposés par l’API.

- `POST /api/mcp/call`
  - Entrée JSON:
    - `tool` (obligatoire)
    - `payload` (optionnel)
  - Appelle le tool enregistré et renvoie la réponse JSON.

## Notes

- Les tools mappent les endpoints MCP déjà exposés (unpublished, sync domains, import PDF, relocate, fix, blueprints).

## Références code

- Endpoint MCP: `app.py` (`/api/mcp/tools`, `/api/mcp/call`).
