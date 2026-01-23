# Étape 10 — Journalisation MCP (runs)

Cette étape conserve un historique léger des exécutions MCP afin de faciliter le suivi et le debug.

## Endpoints

- `GET /api/mcp/run/history`
  - Paramètre optionnel: `limit` (défaut 20)
  - Retourne les derniers runs MCP (`status`, `timestamp`, `results`).

## Notes

- L’historique est en mémoire (non persistant).
- Les runs sont enregistrés à chaque appel de `/api/mcp/run`.

## Références code

- Endpoint MCP: `app.py` (`/api/mcp/run/history`).
