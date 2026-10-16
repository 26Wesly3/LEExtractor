# M0 Route Inventory — every existing HTTP surface in the FastAPI bridge

**Baseline:** branch `feature/v3.2`, commit `e601861bde58b641a1161369a7b5c037b59955c1`.
Application factory: `litsearch/web/api.py:193` `def create_app(...)`.
Every route below is registered inside `create_app`, so the full path is
`/api` + the decorator path. The SPA client adds that prefix itself:
`fetch(\`/api${path}\`, ...)` at `web/src/api.js:3`.

**Why this file exists:** `docs/v3.2/06_REUSE_CHANGE_MAP.md:30` requires M0 to
record the current routes, and `docs/v3.2/08_API_DATA_MODEL_SPEC.md:49` states
the rule that makes the record load-bearing: *"现有 search、similar、landscape、
download、export 等路由保持实际基线兼容；不得编造旧路径"* — the existing routes must
stay baseline-compatible and no old path may be invented. The "v3.2 operation"
column below is taken from the target table at
`docs/v3.2/08_API_DATA_MODEL_SPEC.md:23–47`; where that table has no row for an
existing route, this document says **retained legacy (no v3.2 replacement row)**
rather than inventing a mapping.

## 1. Cross-cutting behaviour registered in the same factory

| Concern | Symbol | Location |
|---|---|---|
| App + OpenAPI mounting | `FastAPI(title="LEExtractor Local API", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)` | `api.py:235-236` |
| Host / Origin / body-size guard (all requests) | `@app.middleware("http") async def local_access(request, call_next)` | `api.py:240-274` |
| Loopback host allowlist → 403 `invalid_host` | | `api.py:242-247` |
| Same-origin check → 403 `origin_rejected` | | `api.py:248-259` |
| Body cap `MAX_BODY = 32 * 1024 * 1024` → 413 `body_too_large` | | `api.py:60`, `260-268` |
| Response headers `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cache-Control: no-store` for `/api*` | | `api.py:270-273` |
| Error envelope for domain errors | `{"error": {"code", "message", "detail"}}` with `exc.status` | `api.py:276-279` |
| Validation errors → 422 `invalid_request`, locations only (never the offending input) | | `api.py:281-286` |
| Unexpected errors → 500 `internal_error` | | `api.py:288-290` |
| Static bundle resolution + SPA fallback | `source_dist = .../web/dist`, else `litsearch/web/static` | `api.py:837-838` |
| Startup/shutdown: logging sinks, `service_start`/`service_stop`, `jobs.shutdown()` | `lifespan` | `api.py:222-233` |

**Compatibility gap against the v3.2 error contract (evidence, not a fix):**
`docs/v3.2/08_API_DATA_MODEL_SPEC.md:55` requires the error envelope to contain
`code, message, retryable, details, request_id`. The baseline envelope at
`api.py:278-279` (and `284-286`, `290`) carries only `code`, `message`, `detail`.
Also `docs/v3.2/08_API_DATA_MODEL_SPEC.md:53` requires revision conflicts to
return 409 `REVISION_CONFLICT` with `current_revision`; the baseline uses
`APIError` with per-handler codes (e.g. `api.py:476-486` save, `api.py:544` search).
M0 records this as a measured difference; it is not changed here.

## 2. Route table

`/api` prefix; `pid` = project id, `jid` = job id, `key` = legacy paper key.

| # | Method | Path | Handler symbol | file:line | v3.2 operation it must stay compatible with |
|---|---|---|---|---|---|
| 1 | GET | `/api/health` | `health` | `api.py:292-293` | v3.2 `GET /api/capabilities` (`08:24`) is the new capability surface; this route remains the operator health surface (no v3.2 row renames it) |
| 2 | GET | `/api/health/live` | `health_live` | `api.py:320-321` | liveness; retained legacy |
| 3 | GET | `/api/health/ready` | `health_ready` | `api.py:325-326` | readiness; retained legacy |
| 4 | GET | `/api/stats` | `stats_view` | `api.py:362-363` | local usage counters; retained legacy |
| 5 | POST | `/api/stats/home-view` | `stats_home_view` | `api.py:366-367` | idempotent page-view counter (`entry_id`, `StatsEventInput` `api.py:67-70`) |
| 6 | POST | `/api/stats/search-entry-click` | `stats_search_entry_click` | `api.py:371-372` | idempotent entry-click counter |
| 7 | GET | `/api/stats/stream` | `stats_stream` (inner `events`) | `api.py:382-383`, `390` | SSE live counters; retained legacy |
| 8 | GET | `/api/settings` | `settings` | `api.py:409-410` | v3.2 `GET /api/settings/credentials` (`08:43`) returns configuration status only; this route reports `configured` booleans (`api.py:184-190`) |
| 9 | POST | `/api/settings` | `set_settings` | `api.py:413-414` | v3.2 `PUT /api/settings/credentials/{provider_id}` (`08:42`). **Baseline difference:** the current writer accepts four fixed field names and persists them to `.web-data/settings.json`, re-hydrating `os.environ` on the next start (`api.py:204-212`, `SettingsInput`); the v3.2 contract is session-scoped, allowlisted and never returns the secret (`docs/v3.2/10_SECURITY_PRIVACY.md`) |
| 10 | GET | `/api/projects` | `projects` | `api.py:434-435` | project list; retained (v3.2 `08:25` covers create) |
| 11 | POST | `/api/projects` (201) | `create_project` | `api.py:444-445` | v3.2 `POST /api/projects` → `revision=1` (`08:25`) |
| 12 | POST | `/api/projects/import` (201) | `import_project` | `api.py:448-449` | legacy session import → creates an independent project; the reader path M0 fixtures target (`litsearch/persistence.py:239-245`) |
| 13 | POST | `/api/demo/projects` (201) | `demo_project` | `api.py:461-462` | isolated synthetic demo project (`02:`, `App.vue:258`) |
| 14 | GET | `/api/projects/{pid}` | `project_state` | `api.py:470-471` | v3.2 `GET /api/projects/{pid}` returns project + active job + current snapshot **and** the ResearchAnchor (`08:26`, `08:67`) |
| 15 | POST | `/api/projects/{pid}/save` | `save_project` | `api.py:476-477` | revision-guarded write (`SaveInput.expected_revision`); v3.2 revision/idempotency rules `08:53` |
| 16 | POST | `/api/projects/{pid}/query-plan` | `query_plan` | `api.py:487-488` | retained legacy; v3.2 keeps `query_variants` provenance in the ResearchAnchor (`08:15`) |
| 17 | GET | `/api/projects/{pid}/papers` | `papers` | `api.py:504-505` | v3.2 `GET /api/projects/{pid}/papers` — snapshot pagination, main-recommendation / exploration / excluded filters (`08:35`) |
| 18 | GET | `/api/projects/{pid}/facets` | `facets` | `api.py:528-529` | retained legacy (no v3.2 row) |
| 19 | GET | `/api/projects/{pid}/papers/{key}` | `paper_detail` | `api.py:534-535` | v3.2 uses `paper_id`; `paper_key` stays a legacy alias resolved at the API boundary (`08:61`) |
| 20 | POST | `/api/projects/{pid}/search` (202) | `search` | `api.py:544-545` | v3.2 `POST /api/projects/{pid}/discover` (`08:27`). `08:49` names `search` explicitly as a route that must stay baseline-compatible |
| 21 | POST | `/api/projects/{pid}/expand` (202) | `expand` | `api.py:607-608` | v3.2 `POST /api/projects/{pid}/expand` (`08:34`) |
| 22 | POST | `/api/projects/{pid}/similar` (202) | `similar` | `api.py:636-637` | kept; `08:49` names `similar` explicitly |
| 23 | GET | `/api/jobs/{jid}` | `job` | `api.py:661-662` | v3.2 `GET /api/projects/{pid}/jobs/{job_id}` (`08:36`) |
| 24 | POST | `/api/jobs/{jid}/cancel` | `cancel_job` | `api.py:665-666` | v3.2 `POST /api/projects/{pid}/jobs/{job_id}/cancel` — cancel keeps committed results (`08:37`) |
| 25 | GET | `/api/projects/{pid}/landscape` | `landscape` | `api.py:669-670` | kept; `08:49` names `landscape`. v3.2 `GET /api/projects/{pid}/overview` (`08:39`) is the new evidence overview |
| 26 | GET | `/api/projects/{pid}/questions` | `questions` | `api.py:677-678` | retained legacy; v3.2 moves leads into the overview `leads` array (`08:67`) |
| 27 | GET | `/api/projects/{pid}/landscape/path` | `evidence_path` | `api.py:686-687` | evidence path between two papers; retained legacy (no v3.2 row) |
| 28 | GET | `/api/projects/{pid}/review` | `review` | `api.py:699-700` | review queue by stage (`title_abstract` \| `full_text`); v3.2 `POST /api/projects/{pid}/judgements` (`08:32`) is the write side |
| 29 | POST | `/api/projects/{pid}/screening-decisions` | `decisions` | `api.py:712-713` | v3.2 `POST /api/projects/{pid}/judgements` — atomic single judgement returning the new revision + rerank `job_id` (`08:32`) |
| 30 | POST | `/api/projects/{pid}/calibration` | `calibration` | `api.py:737-738` | retained legacy (no v3.2 row); the session keeps the calibration record (`litsearch/persistence.py:180-184`) |
| 31 | POST | `/api/projects/{pid}/downloads` (202) | `downloads` | `api.py:755-756` | kept; `08:49` names `download`. v3.2 adds `.../papers/{paper_id}/access-options` (`08:40`) |
| 32 | POST | `/api/projects/{pid}/exports` | `exports` | `api.py:799-800` | kept; `08:49` names `export` |
| 33 | GET | `/api/files/{file_id}` | `controlled_file` | `api.py:832-833` | controlled download of a stored file (`FileResponse`, repository-resolved) |
| 34 | GET | `/{requested:path}` (`include_in_schema=False`) | `spa` | `api.py:840-841` | SPA fallback; `api/` and `api` are refused with 404 `not_found` (`api.py:842-843`), path traversal is refused via `is_relative_to(dist)` (`api.py:844-846`), missing bundle → 503 `frontend_not_built` (`api.py:851-853`) |
| — | GET | `/api/docs`, `/api/openapi.json` | FastAPI built-ins | `api.py:235-236` | `redoc_url=None` |

Route count, measured with
`Select-String -Path litsearch/web/api.py -Pattern '^\s*@app\.'` → **38 decorator
lines**, made up of 1 `@app.middleware` (`api.py:240`), 3 `@app.exception_handler`
(`api.py:276`, `281`, `288`) and **34 routes** (33 under `/api` plus the SPA
catch-all at `api.py:840`). FastAPI additionally serves the two built-in docs
routes declared at `api.py:235-236`, which are not counted in the 34.

## 3. v3.2 contract delta (verified against the shipped OpenAPI contract)

The v3.2 pack ships a real machine-readable contract:
`litsearch/contracts/openapi.json`, `openapi: 3.1.0`,
`info.title: "LEExtractor incremental API 3.2"`, **24 paths**, read with
`json.loads(...)['paths']`. This replaces the "not verified" position: the
contract exists at this commit. (`docs/v3.2/08_API_DATA_MODEL_SPEC.md:5` names it
as `contracts/openapi.json`; in the working tree the file lives at
`litsearch/contracts/openapi.json` alongside `defaults.json`,
`domain.schema.json`, `ai_rerank.schema.json`, `access_links.json` and
`job_states.json`. There is no `docs/v3.2/contracts/` directory in this working
tree — `Get-ChildItem docs/v3.2/contracts` returns nothing.)

**Exact path+method overlap with the baseline (must stay compatible): 4**

| v3.2 contract path | Baseline route |
|---|---|
| `POST /api/projects` | #11 `create_project` (`api.py:444`) |
| `GET /api/projects/{pid}` | #14 `project_state` (`api.py:470`) |
| `POST /api/projects/{pid}/expand` | #21 `expand` (`api.py:607`) |
| `GET /api/projects/{pid}/papers` | #17 `papers` (`api.py:504`) |

**Same operation, different path in the baseline: 2**

| v3.2 contract path | Baseline equivalent |
|---|---|
| `GET /api/projects/{pid}/jobs/{job_id}` | #23 `GET /api/jobs/{jid}` (`api.py:661`) |
| `POST /api/projects/{pid}/jobs/{job_id}/cancel` | #24 `POST /api/jobs/{jid}/cancel` (`api.py:665`) |

**Genuinely new: 18** — `GET /api/capabilities`,
`POST /api/projects/{pid}/discover`,
`POST /api/projects/{pid}/papers/import`,
`GET /api/projects/{pid}/imports/{import_id}`,
`POST /api/projects/{pid}/imports/{import_id}/resolve`,
`POST /api/projects/{pid}/imports/{import_id}/retry`,
`POST /api/projects/{pid}/judgements`, `POST /api/projects/{pid}/rerank`,
`GET /api/projects/{pid}/jobs/{job_id}/zotero-results`,
`GET /api/projects/{pid}/overview`,
`GET /api/projects/{pid}/papers/{paper_id}/access-options`,
`GET /api/projects/{pid}/papers/{paper_id}/venue-metrics`,
`GET /api/session`, `GET /api/settings/credentials`,
`PUT /api/settings/credentials/{provider_id}`,
`DELETE /api/settings/credentials/{provider_id}`,
`POST /api/projects/{pid}/zotero/import`,
`POST /api/projects/{pid}/zotero/export-preview`,
`POST /api/projects/{pid}/zotero/export`.
(19 paths listed for 18 "new" slots because the contract merges `PUT` and
`DELETE` on `/api/settings/credentials/{provider_id}` into one path item.)

**Baseline routes with no v3.2 contract row (retained legacy, must not be
removed without a deprecation record — `docs/v3.2/08_API_DATA_MODEL_SPEC.md:61`):**
`/api/health`, `/api/health/live`, `/api/health/ready`, `/api/stats*`,
`/api/settings` (GET/POST), `/api/projects/import`, `/api/demo/projects`,
`/api/projects/{pid}/save`, `/api/projects/{pid}/query-plan`,
`/api/projects/{pid}/facets`, `/api/projects/{pid}/papers/{key}`,
`/api/projects/{pid}/search`, `/api/projects/{pid}/similar`,
`/api/projects/{pid}/landscape`, `/api/projects/{pid}/landscape/path`,
`/api/projects/{pid}/questions`, `/api/projects/{pid}/review`,
`/api/projects/{pid}/screening-decisions`, `/api/projects/{pid}/calibration`,
`/api/projects/{pid}/downloads`, `/api/projects/{pid}/exports`,
`/api/files/{file_id}`, SPA `/{requested:path}`.
`docs/v3.2/08_API_DATA_MODEL_SPEC.md:49` singles out
search / similar / landscape / download / export as ones that must stay
baseline-compatible, so `/api/projects/{pid}/search` and
`/api/projects/{pid}/similar` must keep serving the existing Web client even
though the contract introduces `discover`.

## 4. How the inventory was produced

```
git -C "<repo>" rev-parse --abbrev-ref HEAD        # feature/v3.2
git -C "<repo>" log -1 --format=%H%n%ad%n%s        # e601861... / 2026-10-15 16:13:26 +0800
grep -n "^\s*@app\." litsearch/web/api.py          # decorator lines quoted above
grep -n "^\s*(async )?def " litsearch/web/api.py   # handler symbols quoted above
```

No route in this document was inferred from the frontend; each one was read from
the decorator line and its immediately following `def`.
