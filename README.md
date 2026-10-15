# LEExtractor

[简体中文](README.zh-CN.md) · **English**

From research questions to traceable literature evidence.

**v0.9.14** connects the v0.9.6 interface design to the v0.9.5 algorithms with Vue 3, Vuetify and a local FastAPI service, and adds concurrent provider fetching, per-stage search timings, a one-line result summary and a live home operating overview. The existing Streamlit and MCP entries remain available.

<p>
  <img src="https://img.shields.io/github/v/release/26Wesly3/LEExtractor?style=flat&labelColor=18181B&color=0B4FA1&label=latest%20release" alt="Latest release">
  <img src="https://img.shields.io/github/downloads/26Wesly3/LEExtractor/total?style=flat&labelColor=18181B&color=0B4FA1&label=total%20downloads" alt="Total downloads">
  <img src="https://img.shields.io/github/stars/26Wesly3/LEExtractor?style=flat&labelColor=18181B&color=334155&label=stars" alt="GitHub stars">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3DA639?style=flat&labelColor=18181B" alt="License: MIT"></a>
</p>

| | |
|---|---|
| **Live demo** | <https://leextractor.duckdns.org> |
| **Windows portable** | [LEExtractor_v0.9.14_Windows_portable.zip](download/LEExtractor_v0.9.14_Windows_portable.zip) · [Releases](https://github.com/26Wesly3/LEExtractor/releases) |
| **Demo recording** | [real local run, 40 records](docs/demo/leextractor-demo.gif) |
| **Benchmark** | BEIR SciFact · 5,183 candidates · 30 queries · 150 real rankings |

![LEExtractor in real use: enter a Chinese research intent, review the translation and per-database queries, run the multi-provider search, then browse results, the evidence landscape, candidate questions, human screening and export](docs/demo/leextractor-demo.gif)

*Real screen recording · hot sample "LLM hallucination" · 40 real records · 3 providers in 8.0 s (2 succeeded / 1 rate-limited) · [lighter GIF](docs/demo/leextractor-demo-small.gif)*

![LEExtractor product concept: a literature search on embodied AI and robot generalization, with key findings, evidence and source links](docs/assets/leextractor-hero-4k.png)

*Product concept*

![Four-step concept workflow: ask a research question, search for relevant studies, read key findings, and verify evidence against the original source](docs/assets/leextractor-workflow-4k.png)

*Exploration flow*

> **The complete readme is bilingual:** [README.en.md](README.en.md) (English) and [README.zh-CN.md](README.zh-CN.md) (简体中文). This page is the short English entry point — the full versions add the badge header, the four-step table, the per-provider notes and the roadmap.

## Get started

**Windows portable (recommended).** [Download the ZIP](download/LEExtractor_v0.9.14_Windows_portable.zip), extract it fully and double-click `Start_LEExtractor.bat`. It creates a local environment if needed, prepares both local models (downloading missing files), and opens <http://127.0.0.1:8012>. Keep the window open during the first download; later launches reuse the cache. The GitHub source and releases include the built interface, so **running needs no Node.js, no GPU and no inference API key**.

**From source.** Install 64-bit Python 3.10+ (3.12 recommended) with Python on PATH, then clone or download this repository via Code → Download ZIP:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_web.py --port 8000
```

On macOS/Linux, create and activate a Python virtual environment, install the same dependencies, and run `python scripts/launch_web.py --port 8000`. Add `--skip-models` to open the UI without preparing models. Preparation failures are reported and leave the UI available for manual English queries and lexical ranking. If the browser does not open, visit the URL manually. Use `启动Web版.bat --port 8001` for an occupied port; Ctrl+C stops the service.

The standard source ZIP excludes weights. The separate model-inclusive handoff ZIP contains all runtime weights and tokenizers; the launcher automatically selects its `models` directory. Initial Python dependency installation and actual literature retrieval still require internet access.

Use **Create offline demo** to explore a separate, clearly labeled synthetic project. For research, create a regular project and configure provider credentials in Settings or a local environment file. Provider availability and rate limits affect coverage.

## Local research translation and semantic ranking

The Web workflow translates Chinese research directions with a local quantized Marian model and lets you edit the English query. The direction drives both retrieval and ranking. CPU FastEmbed uses `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, encoding titles together with abstract chunks. Missing abstracts are labeled. Runtime files total about 116 MB for translation and 267 MB for embeddings including tokenizers. Pinned revisions and SHA-256 inventories are in `litsearch/translation_pins.json` and `litsearch/embedding_pins.json`.

Translation normally uses `~/.cache/leextractor`; embeddings reuse `fastembed_cache` under the system temporary directory. Override them with `LEEXTRACTOR_MODEL_DIR` and `LEEXTRACTOR_EMBEDDING_CACHE`. Bundled models are selected automatically unless these variables are already set.

Six selectable providers include OpenReview public submissions and Google Scholar via SerpApi. CCF 2026 venue classifications support A/B/C filtering; venue rank does not verify paper acceptance or full-paper eligibility. Google Scholar requires a SerpApi key and is selected by default only when configured. Its snippets are not full abstracts. Provider rate limits and access challenges can prevent retrieval; coverage is not guaranteed.

## Current workflow

1. Enter a topic, research direction and year range. Inspect the backend query plan before starting a search.
2. Browse and filter actual project records, inspect metadata, discovery trails and the context of relevance scores.
3. Select seeds for citation expansion or BC/CC similarity. Background tasks expose real stage/count progress, request budgets and cancellation.
4. Inspect the corpus landscape and graph paths; review evidence-linked candidate questions.
5. Make human screening decisions, label calibration examples and obtain available open full texts.
6. Export CSV, RIS, BibTeX, session JSON, PRISMA flow data or an evidence pack. Restore sessions as separate projects.

Project state, task checkpoints and generated files are stored locally in `.web-data`. Browser tabs use revision checks to prevent stale overwrites. The service binds to loopback and does not provide accounts or authentication for a public multiuser deployment.

Search queries and publication identifiers are sent to the selected literature providers. Full-text requests go to open-access sources. Credential responses contain configured flags only. This version does not call an LLM service. Removing the local data directory after stopping the service removes saved Web projects and exports.

## Benchmark

| System | nDCG@20 ↑ | Recall@50 ↑ | P@20 ↑ | MRR@50 ↑ |
| --- | ---: | ---: | ---: | ---: |
| **Current hybrid ranking** | **0.6800** | **0.8767** | **0.0467** | **0.6379** |
| BM25 keyword reference | 0.6631 | 0.8433 | 0.0450 | 0.6367 |
| Previous keyword ranking | 0.5941 | 0.8100 | 0.0450 | 0.5339 |

BEIR SciFact test, all 5,183 candidates, 30 queries sampled with seed 42 out of 300, 150 real rankings, labels from the public qrels — no generated labels, no trained model, no tuning against the score. Against the project's own previous keyword ranking nDCG@20 improves by **+0.0859** (paired bootstrap 95% interval `[0.0080, 0.1773]`, does not cross zero); against **BM25** the gain is only +0.0169 with interval `[-0.0612, 0.1015]`, which crosses zero, so **we do not claim a stable win over BM25**.

```powershell
python -m scripts.run_public_benchmark --queries 30 --seed 42   # --queries 0 for all 300
```

## Scope and evidence

Relevance is a local semantic and lexical ranking signal, not a screening decision or proof of research quality. Citation edges and derived similarity relations are counted separately. Failed, canceled, budget-limited and truncated retrievals remain distinguishable. Full-text inclusion requires a human reading confirmation.

Local title-and-abstract embeddings rerank provider candidates; they cannot recover papers absent from upstream results. Automated full-text synthesis, innovation scoring and Zotero synchronization are planned. Candidate questions and clusters summarize the available corpus; they are not verified scientific conclusions. Counts describe records/reports rather than deduplicated studies.

## Development and validation

- [Setup and current capabilities](README.dev.md)
- [Web integration and API boundaries](docs/web-integration.md)
- [Architecture](ARCHITECTURE.md) · [changes](CHANGES.md) · [roadmap](ROADMAP.md) · [team handoff](ONBOARDING.md) · [deployment](DEPLOYMENT.md)
- [Per-version engineering records](docs/devlog/)

Benchmark pilot: see [public SciFact ranking comparisons, live topic snapshots and human annotation](benchmarks/PILOT_GUIDE.md). [First pilot results and handoff, 2026-10-10](benchmarks/results/scifact-pilot-2026-10-10/SUMMARY.zh-CN.md): 5,183 candidates and 30 seeded test questions, with frozen rankings and per-query metrics for offline rescoring.

Frontend development: `cd web`, `npm ci`, `npm run dev`; start FastAPI separately on port 8000. The Vite proxy forwards `/api`. Before releasing, run `npm run build`, then `python scripts/sync_web_assets.py` from the repository root.

Report issues with the version, reproduction steps, expected result and actual result. Remove secrets from shared logs and sessions.

## Contributors and licence

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

Publication content and third-party data remain subject to their respective terms. Released under the [MIT License](LICENSE); third-party notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
