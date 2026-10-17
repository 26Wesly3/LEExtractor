<div align="center">

# LEExtractor

<a id="preview"></a>

![LEExtractor product concept: a literature search on embodied AI and robot generalization, with key findings, evidence and source links](docs/assets/leextractor-hero-4k.png)

*Product concept*

[简体中文](README.zh-CN.md) · **English**

**From a fuzzy research intent to a traceable evidence chain.**

Search literature. Trace evidence. Build understanding.

<p>
  <img src="https://img.shields.io/badge/status-public%20beta-64748B?style=flat&labelColor=18181B" alt="Status: public beta">
  <img src="https://img.shields.io/badge/focus-literature%20%26%20evidence-334155?style=flat&labelColor=18181B" alt="Focus: literature and evidence">
  <img src="https://img.shields.io/badge/version-v0.9.22-0B4FA1?style=flat&labelColor=18181B" alt="Version: v0.9.22">
</p>

<p>
  <img src="https://img.shields.io/github/v/release/26Wesly3/LEExtractor?style=flat&labelColor=18181B&color=0B4FA1&label=latest%20release" alt="Latest release">
  <img src="https://img.shields.io/github/downloads/26Wesly3/LEExtractor/total?style=flat&labelColor=18181B&color=0B4FA1&label=total%20downloads" alt="Total downloads">
  <img src="https://img.shields.io/github/stars/26Wesly3/LEExtractor?style=flat&labelColor=18181B&color=334155&label=stars" alt="GitHub stars">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-3DA639?style=flat&labelColor=18181B" alt="License: MIT"></a>
  <a href="#start"><img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-334155?style=flat&labelColor=18181B" alt="Supported platforms"></a>
</p>

[Live demo](https://leextractor.duckdns.org) · [Preview](#preview) · [Core experience](#experience) · [Get started](#start) · [Benchmark](#benchmark) · [Roadmap](#roadmap) · [Contributing](#contributing)

</div>

<br>

![LEExtractor in real use: enter a Chinese research intent, review the translation and per-database queries, run the multi-provider search, then browse results, the evidence landscape, candidate questions, human screening and export](docs/demo/leextractor-demo.gif)

*Real screen recording · hot sample "LLM hallucination" · 40 real records · 3 providers in 8.0 s (2 succeeded / 1 rate-limited) · [lighter GIF](docs/demo/leextractor-demo-small.gif)*

---

LEExtractor is built for the early exploration stage of research: it turns one fuzzy research intent into a literature search that has a **path, an identity and a basis**, and leaves the whole trail reviewable by the next person.

**Give every exploration a basis you can revisit.**

> The current build is live at <https://leextractor.duckdns.org>, and the Windows portable package can be downloaded and run directly. Remaining work and limits are listed in [ROADMAP.md](ROADMAP.md) and under "Scope and limits" below.

**Four-step workflow**

![Four-step concept workflow: ask a research question, search for relevant studies, read key findings, and verify evidence against the original source](docs/assets/leextractor-workflow-4k.png)

*Exploration flow*

| Step | What you do | What the system leaves behind |
| --- | --- | --- |
| **01 · Ask** | Enter a research topic or a specific question | Intent split into object / method / task / scenario; Chinese directions translated locally, translation editable |
| **02 · Search** | Run a multi-provider search | Per-database queries are shown first; six providers fetched concurrently, deduplicated on canonical identifiers, per-provider status visible |
| **03 · Read** | Browse records and their discovery basis | Each record shows provider, score context and discovery route (search / citation / coupling / co-citation) |
| **04 · Verify** | Return to the source and the evidence | Citation evidence graph, directed evidence paths, external links, and exports that import into Zotero |

<a id="experience"></a>

## Core experience

The capabilities below are **already implemented**, not planned; each one states its own limits.

### Search around a research question

Start from one natural-language description and see which words the system placed in which slots, and what each database will receive. Unrecognised slots are shown as "unrecognised" instead of inventing a plausible word; when a Chinese question is paired with English keywords, the queries keep the keywords and the parse is recorded only.

### Give failure a voice

Rate limits, timeouts and auth failures are marked per provider, cache hits keep their native identity, and nothing collapses into "0 papers". `TRUNCATED` (caller-side limit, results usable) and `PARTIAL` (interrupted, must not be used as coverage proof) stay distinct. The diagnostics panel expands into per-stage timings and per-provider status.

### Keep the discovery trail

Every record carries a discovery trace: which provider, which round, which query, which score, which evidence. Citation edges are stored separately from derived and semantic edges, and PageRank / evidence paths run on citation edges only.

### Derive candidate questions from evidence

Three generators — combination gap, coverage gap, unfollowed result — each producing rationale, supporting papers, nearest existing work, risks and a suggested next query, and **always labelled as a hypothesis to verify**. Below six records, no question is produced at all.

### Hand the judgement and the artefacts back to you

Ranking is a ranking signal, not a screening decision: human screening, threshold calibration and full-text retrieval each keep their own facts. The evidence pack contains `AGENT_HANDOFF.md`, `discovery_trace.jsonl`, `evidence_graph.json`, `landscape.json` and `questions.json`, ready for Zotero or for Codex / Qoder / WorkBuddy.

<a id="start"></a>

## Get started

### Live demo

<https://leextractor.duckdns.org>

### Windows portable (recommended)

1. Download and **fully extract** [`LEExtractor_v0.9.22_Windows_portable.zip`](download/LEExtractor_v0.9.22_Windows_portable.zip) (or grab it from [Releases](https://github.com/26Wesly3/LEExtractor/releases)).
2. Double-click **`Start_LEExtractor.bat`**.
3. The browser opens <http://127.0.0.1:8012>. Keep the console window open while using it.

The launcher prefers the bundled `runtime\`, then `.venv`, then the system Python, and installs missing dependencies locally. **No Node.js, no GPU and no inference API key are needed to run it.**

The minimal package ships without model weights: without them **lexical retrieval, provider filtering, evidence trails and exports all work**; only Chinese translation (≈116 MB) and semantic ranking (≈267 MB) download on first use and are cached afterwards. If port 8012 is taken, `set LEEXTRACTOR_PORT=8013` first.

### From source

```powershell
git clone https://github.com/26Wesly3/LEExtractor.git
cd LEExtractor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_web.py --port 8000
```

macOS / Linux: same, with `python scripts/launch_web.py --port 8000`. Add `--skip-models` to open the UI only.

### Configure providers

S2 / OpenAlex / SerpApi / Unpaywall credentials go in the in-app **Settings** page, or copy `.env.example` to `.env`. **Never commit `.env`.** Without keys, rate limits can make results incomplete — the diagnostics panel tells you why.

### An example question

```text
Hallucination and retrieval-augmented generation in large language models
```

→ Translated and expanded into per-database queries: an OpenAlex keyword string, an arXiv field-qualified expression, and Crossref metadata completion — each counted and deduplicated separately.

<a id="benchmark"></a>

## Benchmark

BEIR SciFact test, all 5,183 candidates, 30 queries sampled with seed 42 out of 300, 150 real rankings; labels come straight from the public qrels — no generated labels, no trained model, no tuning against the score.

| System | nDCG@20 ↑ | Recall@50 ↑ | P@20 ↑ | MRR@50 ↑ |
| --- | ---: | ---: | ---: | ---: |
| **Current hybrid ranking** | **0.6800** | **0.8767** | **0.0467** | **0.6379** |
| BM25 keyword reference | 0.6631 | 0.8433 | 0.0450 | 0.6367 |
| Previous keyword ranking | 0.5941 | 0.8100 | 0.0450 | 0.5339 |
| Local vectors, titles only | 0.5438 | 0.7300 | 0.0350 | 0.5040 |
| Local vectors, title + abstract | 0.4856 | 0.7367 | 0.0383 | 0.4268 |

**How to read it (we do not overclaim)**: against the project's own previous keyword ranking, nDCG@20 improves by **+0.0859**, paired bootstrap 95% interval `[0.0080, 0.1773]` (does not cross zero). Against **BM25** the gain is only +0.0169 with interval `[-0.0612, 0.1015]` — **it crosses zero, so we do not claim a stable win over BM25.** Full per-query results and the replay procedure are in [`benchmarks/results/scifact-pilot-2026-10-10/`](benchmarks/results/scifact-pilot-2026-10-10/SUMMARY.zh-CN.md).

```powershell
python -m scripts.run_public_benchmark --queries 30 --seed 42   # --queries 0 for all 300
```

## Scope and limits

- Relevance is a **ranking signal**, not a screening decision or a quality judgement.
- Semantic reranking only reorders what upstream already returned; it **cannot recover papers the providers never returned**.
- Counts are records/reports; multiple reports of one study are not merged to study level.
- Automated full-text synthesis, novelty scoring, Zotero two-way sync, institutional access and multi-user isolation are **not implemented**.
- This is a single-user local (or single-instance cloud) workflow, not a multi-tenant service.

<a id="roadmap"></a>

## Roadmap

| Status | Content |
| --- | --- |
| **In progress** | Real-scenario user testing and data collection; semantic enhancement under comparable conditions (RRF / weighted sum ablation) |
| `pending_external` | Frozen 3–5 case datasets with human gold labels; real provider smoke with credentials |
| `planned` | Five retrieval baselines; Graph Benchmark; full-corpus vector recall |
| `deferred` | Zotero two-way sync; institutional access; GROBID full-text structuring; decoupling screening from PRISMA; multi-user isolation |

The full three-state list is in [ROADMAP.md](ROADMAP.md); per-version changes are in [CHANGES.md](CHANGES.md).

## Layout

```text
app.py                  Streamlit entry point (kept)
litsearch/              domain algorithms and providers
  sources.py            six-provider fetching and unified transport
  retrieval.py          RetrievalResult status semantics
  filters.py            lexical + semantic fused ranking
  intent.py             research intent parsing and per-database queries
  prisma.py             two-stage screening and flow counts
  landscape.py          research landscape
  questions.py          candidate research questions
  web/                  FastAPI service and job management
web/                    Vue 3 + Vuetify workspace source
scripts/                launch, packaging, benchmark
benchmarks/             evaluation data, guides and measured results
docs/                   DevLog, ADRs, image assets, demo recording
download/               Windows portable download
```

## Development and validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # offline tests
.\.venv\Scripts\ruff.exe check .                 # lint
python -m scripts.launch_web --port 8000         # web workspace
python server.py                                 # MCP (12 tools)
```

Frontend: `cd web && npm ci && npm run dev`; before releasing run `npm run build` then `python scripts/sync_web_assets.py`.

| Document | Contents |
| --- | --- |
| [CHANGES.md](CHANGES.md) | Every change from v0.3.0 to the current release |
| [CHANGELOG.md](CHANGELOG.md) | User-facing version-level changes |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules and invariants |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Server deployment, rollback and health checks |
| [ONBOARDING.md](ONBOARDING.md) | Team handoff |
| [docs/devlog/](docs/devlog/) | Per-version engineering records (Before / After) |

<a id="contributing"></a>

## Contributing

Issues and pull requests are welcome. When reporting a problem, include the **version, reproduction steps, expected result and actual result**; remove secrets (`.env`, API keys, institutional credentials) before sharing logs or sessions.

## Contributors and licence

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

Released under the **MIT License** — see [LICENSE](LICENSE). Third-party data and model weights are not covered by it — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
