# Étape 1 — Inventaire des endpoints et données (MCP)

Ce document liste les endpoints UI/API et les tables/colonnes qui supportent le flux manuel actuel (reports → domains → import PDF → relocate → fix → blueprints). Il servira de base pour exposer ces actions en tools MCP.

## 1) Endpoints UI (pages)

- `GET /reports` — tableau de bord pour sélectionner une certification non publiée et vérifier la qualité des questions.【F:app.py†L1944-L2039】
- `GET /modules/` — page Domains (import/génération des domaines).【F:dom.py†L7-L9】
- `GET /pdf/` — page Import PDF de questions (sélection PDF + import).【F:pdf_importer.py†L18-L36】
- `GET /reloc/` — page Relocalisation IA (déplacement des questions par domaine).【F:reloc.py†L15-L23】
- `GET /fix` — page Fix Questions (correction des QCM sans bonnes réponses).【F:app.py†L2092-L2119】
- `GET /blueprints/` — page Blueprints (génération/édition des blueprints par domaine).【F:module_blueprints.py†L50-L56】

## 2) Endpoints API (dropdowns et opérations)

### A. Domains / Modules (page domains)
- `GET /modules/api/providers` — liste des providers.【F:dom.py†L10-L17】
- `GET /modules/api/certifications/<prov_id>` — certifications d’un provider (avec `code_cert_key`/`code_cert`).【F:dom.py†L19-L25】
- `GET /modules/api/certifications/<cert_id>/modules` — domaines d’une certification.【F:dom.py†L28-L36】
- `GET /modules/api/default-module?code_cert=...` — domaine default associé à un `code_cert`.【F:dom.py†L39-L61】
- `POST /modules/api/modules` — création d’un domaine (module).【F:dom.py†L64-L91】
- `POST /modules/api/certifications/<cert_id>/generate-domains` — génération IA des domaines (outline officiel).【F:dom.py†L94-L137】
- `POST /modules/api/mcp/certifications/<cert_id>/sync-domains` — génération IA + synchronisation des domaines (MCP).【F:dom.py†L140-L223】

### B. Import PDF (page import PDF)
- `POST /pdf/generate-questions` — extraction + génération IA des questions depuis un PDF (session en mémoire).【F:pdf_importer.py†L494-L612】
- `POST /pdf/import-questions` — insertion des questions générées en DB (module/domaine cible).【F:pdf_importer.py†L814-L848】
- `POST /pdf/export/questions` — export PDF d’un set de questions (optionnel).【F:pdf_importer.py†L615-L736】
- `POST /pdf/api/mcp/import-local` — import MCP depuis PDF local (résolution domaine default).【F:pdf_importer.py†L455-L563】

### C. Relocalisation (page relocate)
- `GET /reloc/api/providers` — liste des providers.【F:reloc.py†L22-L30】
- `GET /reloc/api/certifications/<prov_id>` — certifications d’un provider (avec `code_cert_key`/`code_cert`).【F:reloc.py†L32-L39】
- `GET /reloc/api/modules/<cert_id>` — domaines d’une certification.【F:reloc.py†L41-L49】
- `GET /reloc/api/question_count/<module_id>` — nombre de questions dans un module source.【F:reloc.py†L52-L59】
- `GET /reloc/api/stream_relocate` — streaming SSE de relocalisation (assignation IA).【F:reloc.py†L62-L167】
- `POST /reloc/api/mcp/relocate` — relocalisation MCP (batch + workers).【F:reloc.py†L173-L224】

### D. Fix Questions (page fix)
- `POST /fix/get_certifications` — liste des certifications d’un provider (utilisé dans la UI fix).【F:app.py†L2117-L2125】
- `POST /fix/get_progress` — progression des questions à corriger (QCM/drag-n-drop/matching).【F:app.py†L2128-L2134】
- `POST /fix/process` — déclenche l’exécution du workflow de correction (job).【F:app.py†L2360-L2389】
- `GET /fix/status/<job_id>` — statut du job de correction (polling).【F:app.py†L2444-L2450】
- `POST /api/mcp/fix` — déclenche la correction MCP (assign/drag/matching).【F:app.py†L2461-L2491】
- `GET /api/mcp/fix/status/<job_id>` — statut MCP du job de correction.【F:app.py†L2494-L2499】
- `POST /api/mcp/orchestrate` — plan d’orchestration MCP pour la certification éligible.【F:app.py†L2527-L2616】
- `GET /api/mcp/tools` — liste des tools MCP exposés par l’API.【F:app.py†L2619-L2630】
- `POST /api/mcp/call` — appel d’un tool MCP par nom.【F:app.py†L2633-L2660】
- `POST /api/mcp/run` — exécution séquentielle du plan MCP.【F:app.py†L2663-L2691】
- `GET /api/mcp/run/history` — historique des exécutions MCP.【F:app.py†L2704-L2711】
- `POST /api/mcp/schedule` — exécution planifiée via MCP.【F:app.py†L1944-L1959】
- `GET /api/mcp/schedule/status/<job_id>` — statut du job planifié MCP.【F:app.py†L1962-L1966】

### E. Blueprints (page blueprints)
- `GET /blueprints/api/providers` — liste des providers.【F:module_blueprints.py†L58-L61】
- `GET /blueprints/api/certifications/<prov_id>` — certifications d’un provider (avec `code_cert_key`/`code_cert`).【F:module_blueprints.py†L63-L69】
- `GET /blueprints/api/certifications/<cert_id>/modules` — domaines + blueprint existant.【F:module_blueprints.py†L72-L78】
- `PATCH /blueprints/api/modules/<module_id>` — mise à jour d’un blueprint (module).【F:module_blueprints.py†L95-L118】
- `PATCH /blueprints/api/certifications/<cert_id>/modules` — mise à jour en masse des blueprints.【F:module_blueprints.py†L128-L184】
- `POST /blueprints/api/certifications/<cert_id>/generate-blueprints` — génération IA des blueprints.【F:module_blueprints.py†L242-L355】
- `POST /blueprints/api/mcp/certifications/<cert_id>/generate-blueprints` — génération IA des blueprints (MCP).【F:module_blueprints.py†L377-L383】

## 3) Tables & colonnes DB utilisées

### Tables principales
- `provs` — fournisseurs (providers).【F:dom.py†L10-L17】
- `courses` — certifications (champs clés : `id`, `name`, `prov`, `code_cert_key` (code_cert), `pub`).【F:dom.py†L19-L25】【F:db.py†L560-L610】
- `modules` — domaines (champs clés : `id`, `name`, `descr`, `course`, `code_cert`, `blueprint`).【F:dom.py†L28-L36】【F:module_blueprints.py†L72-L78】
- `questions` — questions (champs clés : `id`, `text`, `level`, `nature`, `ty`, `module`, `src_file`).【F:db.py†L742-L812】
- `answers` — réponses (texte de réponse).【F:db.py†L951-L978】
- `quest_ans` — mapping questions ↔ réponses (`isok` pour la bonne réponse).【F:db.py†L951-L978】

### Tables utilisées par les rapports (qualité & publication)
- `courses.pub` — utilisé pour détecter les certifications non publiées (`pub != 1`), avec `pub = 2` pour l’éligibilité automatique MCP.【F:db.py†L560-L611】
- `modules.code_cert` / `courses.code_cert_key` — association “domain default” pour l’import initial (reports + import).【F:db.py†L560-L610】
- agrégations `questions` + `quest_ans` — détection des questions sans bonne réponse (fix).【F:db.py†L457-L519】

## 4) Endpoints/queries candidats pour MCP (à exposer)

- `list_unpublished_certifications` → basé sur `get_unpublished_certifications_report()` (reports).【F:db.py†L560-L610】
- `sync_domains_from_outline` → basé sur `generate-domains` + insertion modules (domains).【F:dom.py†L64-L178】
- `import_pdf_questions` → basé sur `/pdf/generate-questions` + `/pdf/import-questions` (import PDF).【F:pdf_importer.py†L494-L848】
- `relocate_questions_by_domain` → basé sur `stream_relocate` (relocate).【F:reloc.py†L62-L167】
- `fix_invalid_questions` → basé sur `/fix/process` + `/fix/status` (fix).【F:app.py†L2360-L2450】
- `generate_domain_blueprints` → basé sur `/generate-blueprints` (blueprints).【F:module_blueprints.py†L242-L355】
