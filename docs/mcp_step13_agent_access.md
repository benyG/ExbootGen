# Étape 13 — Accès agent autorisé au MCP

Ce document décrit comment un agent autorisé peut se connecter au service MCP et invoquer les tools exposés par l’API ExBoot.

## Prérequis

- Avoir un accès réseau au serveur ExBoot (HTTP/HTTPS).
- Disposer des identifiants d’accès (si une passerelle d’authentification est en place).

## Découverte des tools MCP

1. Interroger la liste des tools disponibles :

```
GET /api/mcp/tools
```

2. Le serveur retourne une liste avec le nom du tool, la méthode et l’endpoint.

## Appel d’un tool MCP

1. Utiliser l’endpoint générique d’appel :

```
POST /api/mcp/call
Content-Type: application/json

{
  "tool": "sync_domains",
  "payload": {
    "cert_id": 123
  }
}
```

2. Le serveur renvoie `status`, `status_code` et `result`.

## Orchestration complète

1. Générer un plan :

```
POST /api/mcp/orchestrate
Content-Type: application/json

{
  "cert_id": 123,
  "provider_id": 7,
  "code_cert": "AZ-900"
}
```

2. Exécuter le plan :

```
POST /api/mcp/run
Content-Type: application/json

{
  "cert_id": 123,
  "provider_id": 7,
  "code_cert": "AZ-900"
}
```

## Notes de sécurité

- Le serveur peut être protégé par un reverse proxy (Basic/Auth, JWT, IP allowlist, etc.).
- Les endpoints MCP doivent être réservés aux agents autorisés.

## Références code

- Endpoints MCP: `app.py` (`/api/mcp/tools`, `/api/mcp/call`, `/api/mcp/orchestrate`, `/api/mcp/run`).
