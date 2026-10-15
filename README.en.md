# LEExtractor

[简体中文](README.zh-CN.md) · **English**

> **From a fuzzy research intent to a reviewable evidence chain.**

| | |
|---|---|
| **Live demo** | <https://leextractor.duckdns.org> |
| **Current version** | v0.9.14 (Vue 3 workspace + local FastAPI + MCP) |
| **Windows portable** | [Download](download/LEExtractor_v0.9.14_Windows_portable.zip) · [Releases](https://github.com/26Wesly3/LEExtractor/releases) |
| **Benchmark** | BEIR SciFact · 5,183 candidates · 30 queries · 150 real rankings |

## Interface: concept art vs. the real thing

| Early concept art (design mock-up) | Real recording (running locally) |
|---|---|
| [![Product concept](docs/assets/leextractor-hero.png)](docs/assets/leextractor-hero-4k.png) | ![LEExtractor in real use](docs/demo/leextractor-demo.gif) |
| *Concept* — early interface design, **not a screenshot of the app** | *Real* — LLM-hallucination sample, sources OpenAlex / arXiv / Crossref |

<details>
<summary>See the full workflow concept art (click to expand)</summary>

[![Workflow concept](docs/assets/leextractor-workflow.png)](docs/assets/leextractor-workflow-4k.png)

*Concept* — exploration flow illustration, **not a screenshot of the app**
</details>

> The recording shows **40 real retrieved records** in 8.0 s from 3 providers (2 succeeded, 1 rate-limited), with the interface honestly reporting "partially complete · some providers temporarily limited".
> A [lighter GIF](docs/demo/leextractor-demo-small.gif) is available for slow connections.

---

## What this is

LEExtractor is built for the **early exploration stage of research**: it turns one fuzzy research intent into a literature search that has a **path, an identity and a basis**, and leaves the whole trail reviewable by the next person.

It refuses to fake three things:

- **It does not pretend coverage is complete.** Rate limits, timeouts and auth failures are shown per provider and never collapse into "0 papers".
- **It does not pretend confidence.** No calibration means no "relevance rate" is shown; candidate research questions are always labelled as hypotheses to verify.
- **It does not pretend to be a conclusion.** Citation edges are kept separate from derived/semantic edges; landscape numbers describe only the retrieved corpus.

## What it does

**01 / Search and expand**
One research intent is decomposed into object / method / task / scenario and rendered into each database's own query syntax (S2 / OpenAlex keyword strings, arXiv field-qualified queries, Crossref metadata). Six providers are fetched concurrently, records are deduplicated on canonical identifiers, and citation expansion continues from real references and citations.

**02 / Relations and evidence**
A citation evidence graph (PageRank / communities / directed evidence paths), bibliographic coupling, co-citation and text similarity are counted separately. The research landscape reports topics, temporal trends, novelty and coverage — each with its own boundaries stated.

**03 / Judgement and handoff**
Ranking is a ranking signal, not a screening decision: human screening, calibration and full-text retrieval each keep their own facts. Export CSV / RIS / BibTeX / session JSON / a complete evidence pack (`AGENT_HANDOFF.md`, `discovery_trace.jsonl`, `evidence_graph.json`, `landscape.json`, `questions.json`) that imports into Zotero or hands off to Codex / Qoder / WorkBuddy.

## Quick start

### Option 1 — Windows portable (recommended)

1. Download and **fully extract** [`LEExtractor_v0.9.14_Windows_portable.zip`](download/LEExtractor_v0.9.14_Windows_portable.zip) (or grab it from [Releases](https://github.com/26Wesly3/LEExtractor/releases)).
2. Double-click **`Start_LEExtractor.bat`**.
3. The browser opens <http://127.0.0.1:8012>. Keep the console window open while using it.

The portable launcher first tries its bundled `runtime\`, then `.venv`, then the system Python; missing dependencies trigger an automatic local install. **No Node.js, no GPU and no inference API key are needed to run it.**

> Models: the minimal package ships without weights. Without them **lexical retrieval, provider filtering, evidence trails and exports all work**; only Chinese translation and semantic ranking download on first use (≈116 MB translation, ≈267 MB embedding, cached afterwards). If port 8012 is taken, `set LEEXTRACTOR_PORT=8013` before launching.

### Option 2 — from source

```powershell
git clone https://github.com/26Wesly3/LEExtractor.git
cd LEExtractor
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]" -c constraints.txt
.\.venv\Scripts\python.exe scripts\launch_web.py --port 8000
```

On macOS / Linux use `python scripts/launch_web.py --port 8000`. Add `--skip-models` to open the UI without preparing models.

### Configure providers

S2 / OpenAlex / SerpApi / Unpaywall credentials go in the in-app **Settings** page, or copy `.env.example` to `.env`. **Never commit `.env`.** Without keys, rate limits can make results incomplete — the diagnostics panel tells you why.

## Measured results

BEIR SciFact test, all 5,183 candidates, 30 queries sampled with seed 42 out of 300, 150 real rankings; labels come straight from the public qrels — no generated labels, no trained model, no tuning against the score.

| System | nDCG@20 ↑ | Recall@50 ↑ | P@20 ↑ | MRR@50 ↑ |
|---|---:|---:|---:|---:|
| **Current hybrid ranking** | **0.6800** | **0.8767** | **0.0467** | **0.6379** |
| BM25 keyword reference | 0.6631 | 0.8433 | 0.0450 | 0.6367 |
| Previous keyword ranking | 0.5941 | 0.8100 | 0.0450 | 0.5339 |
| Local vectors, titles only | 0.5438 | 0.7300 | 0.0350 | 0.5040 |
| Local vectors, title + abstract | 0.4856 | 0.7367 | 0.0383 | 0.4268 |

**How to read it**: against the project's own previous keyword ranking, nDCG@20 improves by **+0.0859**, paired bootstrap 95% interval `[0.0080, 0.1773]` (does not cross zero). Against **BM25** the gain is only +0.0169 with interval `[-0.0612, 0.1015]` — **it crosses zero, so we do not claim a stable win over BM25.** Full per-query results and the replay procedure live in [`benchmarks/results/scifact-pilot-2026-10-10/`](benchmarks/results/scifact-pilot-2026-10-10/SUMMARY.zh-CN.md).

```powershell
python -m scripts.run_public_benchmark --queries 30 --seed 42   # --queries 0 for all 300
```

## Scope and limits

- Relevance is a **ranking signal**, not a screening decision or a quality judgement.
- Semantic reranking only reorders what upstream already returned; it **cannot recover papers the providers never returned**.
- Counts are records/reports; multiple reports of one study are not merged to study level.
- Automated full-text synthesis, novelty scoring, Zotero two-way sync, institutional access and multi-user isolation are **not implemented**.
- This is a single-user local (or single-instance cloud) workflow, not a multi-tenant service.

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
docs/                   DevLog, ADRs, image assets
download/               Windows portable download
```

## Development

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # offline tests
.\.venv\Scripts\ruff.exe check .                 # lint
python -m scripts.launch_web --port 8000         # web workspace
python server.py                                 # MCP (12 tools)
```

Frontend: `cd web && npm ci && npm run dev`; before releasing run `npm run build` then `python scripts/sync_web_assets.py`.

## Documentation

| Document | Contents |
|---|---|
| [CHANGES.md](CHANGES.md) | Every change from v0.3.0 to the current release |
| [CHANGELOG.md](CHANGELOG.md) | User-facing version-level changes |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Modules and invariants |
| [ROADMAP.md](ROADMAP.md) | Remaining work (planned / pending_external / deferred) |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Server deployment, rollback and health checks |
| [ONBOARDING.md](ONBOARDING.md) | Team handoff |
| [docs/devlog/](docs/devlog/) | Per-version engineering records (Before / After) |

## Contributors and licence

[GlueGPT](https://github.com/GlueGPT) · [jvligyh](https://github.com/jvligyh)

Publication content and third-party data remain subject to their own terms. **The repository code licence has not yet been selected**, so please do not redistribute or use it commercially without talking to us first.
