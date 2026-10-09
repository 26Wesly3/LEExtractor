# LEExtractor

[简体中文](README.zh-CN.md) · **English**

From research questions to traceable literature evidence.

**v0.9.8** connects the v0.9.6 interface design to the v0.9.5 algorithms with Vue 3, Vuetify and a local FastAPI service. The existing Streamlit and MCP entries remain available.

## Get started

On Windows, double-click **启动LEExtractor.bat** (or **启动Web版.bat**). It creates a Python environment, installs dependencies and opens http://127.0.0.1:8000. The release includes the built interface; Node.js is only needed when changing frontend code.

Manual startup (Python 3.10+):

```sh
python -m pip install -e ".[web]" -c constraints.txt
python -m litsearch.web --port 8000
```

Use **Create offline demo** to explore a separate, clearly labeled synthetic project. For research, create a regular project and configure provider credentials in Settings or a local environment file. Provider availability and rate limits affect coverage.

## Current workflow

1. Enter a topic, research direction and year range. Inspect the backend query plan before starting a search.
2. Browse and filter actual project records, inspect metadata, discovery trails and the context of relevance scores.
3. Select seeds for citation expansion or BC/CC similarity. Background tasks expose real stage/count progress, request budgets and cancellation.
4. Inspect the corpus landscape and graph paths; review evidence-linked candidate questions.
5. Make human screening decisions, label calibration examples and obtain available open full texts.
6. Export CSV, RIS, BibTeX, session JSON, PRISMA flow data or an evidence pack. Restore sessions as separate projects.

Project state, task checkpoints and generated files are stored locally in `.web-data`. Browser tabs use revision checks to prevent stale overwrites. The service binds to loopback and does not provide accounts or authentication for a public multiuser deployment.

Search queries and publication identifiers are sent to the selected literature providers. Full-text requests go to open-access sources. Credential responses contain configured flags only. This version does not call an LLM service. Removing the local data directory after stopping the service removes saved Web projects and exports.

## Scope and evidence

Relevance is a lexical ranking signal, not a screening decision or proof of research quality. Citation edges and derived similarity relations are counted separately. Failed, canceled, budget-limited and truncated retrievals remain distinguishable. Full-text inclusion requires a human reading confirmation.

Semantic embedding retrieval, automated full-text synthesis, innovation scoring and Zotero synchronization are planned. Candidate questions and clusters summarize the available corpus; they are not verified scientific conclusions. Counts describe records/reports rather than deduplicated studies.

The following earlier concept image is illustrative, not a screenshot of current results:

![Product concept](docs/assets/leextractor-hero-4k.png)

## Development and validation

- [Setup and current capabilities](README.dev.md)
- [Web integration and API boundaries](docs/web-integration.md)
- [Current validation](VALIDATION_v0.9.8.md)
- [Architecture](ARCHITECTURE.md), [changes](CHANGES.md), [roadmap](ROADMAP.md), [team handoff](ONBOARDING.md)

Frontend development: `cd web`, `npm ci`, `npm run dev`; start FastAPI separately on port 8000. The Vite proxy forwards `/api`. Before releasing, run `npm run build`, then `python scripts/sync_web_assets.py` from the repository root.

Report issues with the version, reproduction steps, expected result and actual result. Remove secrets from shared logs and sessions.

## Contributors

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

Publication content and third-party data remain subject to their respective terms. A repository code license has not yet been selected.
