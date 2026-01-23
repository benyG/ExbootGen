# Étape 6 — Service MCP: correction des questions

Cette étape expose des endpoints MCP pour lancer la correction automatique des questions (QCM sans bonne réponse, drag-n-drop, matching) et suivre l’avancement via un job.

## Endpoints

- `POST /api/mcp/fix`
  - Entrée JSON:
    - `provider_id` (obligatoire)
    - `cert_id` (obligatoire)
    - `action` (optionnel) : `assign`, `drag`, `matching`
  - Retour : `{ status: "queued", job_id }` (ou exécution inline si Celery indisponible).

- `GET /api/mcp/fix/status/<job_id>`
  - Retourne l’état du job (mêmes données que `/fix/status/<job_id>`).

## Références code

- Endpoint MCP: `app.py` (`/api/mcp/fix`, `/api/mcp/fix/status/<job_id>`).
