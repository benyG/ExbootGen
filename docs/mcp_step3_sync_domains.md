# Étape 3 — Service MCP: synchronisation des domaines

Cette étape expose un endpoint MCP pour générer les domaines via IA et synchroniser la table `modules` (création des domaines manquants, mise à jour des descriptions vides).

## Endpoint

- `POST /modules/api/mcp/certifications/<cert_id>/sync-domains`
  - Génère les domaines avec `generate_domains_outline()`.
  - Insère les domaines manquants dans `modules`.
  - Met à jour la colonne `descr` si elle est vide côté DB.
  - Retourne un résumé (`created`, `updated`, `processed`) et le détail par domaine.

## Règles d’upsert

- Comparaison par `name` (insensible à la casse, trim).
- Si le domaine existe :
  - `descr` est rempli uniquement si elle est vide et que l’IA fournit un texte.
  - Sinon, le domaine est marqué `unchanged`.
- Si le domaine n’existe pas :
  - création d’un module rattaché à la certification.

## Références code

- Endpoint MCP: `dom.py` (`/api/mcp/certifications/<int:cert_id>/sync-domains`).
- Nettoyage des domaines IA: `_clean_generated_modules()`.
