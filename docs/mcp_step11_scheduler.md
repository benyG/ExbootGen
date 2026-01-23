# Étape 11 — Planification MCP

Cette étape expose des endpoints MCP pour déclencher et suivre l’exécution planifiée des actions (schedule).

## Endpoints

- `POST /api/mcp/schedule`
  - Entrée JSON:
    - `date` (optionnel)
    - `entries` (liste d’actions à exécuter)
  - Retour : `{ status, job_id, mode }`

- `GET /api/mcp/schedule/status/<job_id>`
  - Retourne l’état du job planifié (mêmes données que `/schedule/status/<job_id>`).

## Références code

- Endpoints MCP: `app.py` (`/api/mcp/schedule`, `/api/mcp/schedule/status/<job_id>`).
