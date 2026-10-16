# M0 Environment Baseline

All values below were produced by the command quoted next to them, run from
`E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work` on 2026-10-17 (Asia/Shanghai).
Nothing here is remembered or estimated. Anything that could not be measured is
marked **NOT VERIFIED** with the reason.

## 1. Repository state

```
git rev-parse --abbrev-ref HEAD
  feature/v3.2
git log -1 --format=%H%n%ad%n%s
  e601861bde58b641a1161369a7b5c037b59955c1
  Thu Oct 15 16:13:26 2026 +0800
  docs: move the product-concept figure between the title and the language switch, drop the preview section heading
```

`git status --porcelain` (measured 2026-10-17, after the v3.2 work items had
started writing; the same command returned 8 entries at the start of this work
item, which shows the working tree is moving under concurrent v3.2 work):

```
 M pyproject.toml
?? docs/v3.2/
?? fixtures/
?? handoff/
?? litsearch/contracts/
?? litsearch/v32/
?? scripts/validate_v32_pack.py
?? tests/test_v32_contracts.py
?? tests/test_v32_core_rank.py
?? tests/test_v32_legacy_fixtures.py
?? tests/test_v32_secret_hygiene.py
?? tests/test_v32_store.py
```

`git diff --stat` → `pyproject.toml | 1 +` (one insertion). `git diff -- pyproject.toml`:

```diff
 [tool.setuptools.package-data]
 "litsearch" = ["translation_pins.json", "embedding_pins.json", "ccf_catalog.json"]
+"litsearch.contracts" = ["*.json"]
 "litsearch.web" = ["static/**/*", "static/*"]
```

So the single tracked change on top of `e601861b` **at measurement time** was
packaging the new `litsearch/contracts/*.json` into the wheel.

**This work item made no commit.** The Lead commits, and did so while the work
item was running, so HEAD moved. Both facts are recorded because M0's value
depends on knowing exactly which commit the baseline describes:

| Observation | Value |
|---|---|
| **M0 measurement commit** (the pre-v3.2 working state every number in these QA documents describes) | `e601861bde58b641a1161369a7b5c037b59955c1` — `docs: move the product-concept figure between the title and the language switch, drop the preview section heading`, Thu Oct 15 16:13:26 2026 +0800 |
| HEAD at the end of this work item | `08888e1326d27ec0a144e718789c471dcc3983a2` — `feat(v3.2): job scheduler, M5 adapters and M0 baseline evidence`, Sat Oct 17 01:02:34 2026 +0800 |
| Relationship | `git merge-base --is-ancestor e601861b HEAD` → exit 0, so the baseline commit is a direct ancestor of the new HEAD |
| Commits the Lead added in between | `fb6b866` M0 contract layer / project store / sessions / secret guard · `eb08ea7` CoreRank + feedback · `b1856a5` PDF import, AI rerank, domain map, overview · `08888e1` job scheduler, M5 adapters, M0 baseline evidence |
| Working tree at the end (not written by this work item) | ` M web/src/main.js` (29 insertions, 2 deletions), `?? litsearch/v32/api.py`, `?? web/src/v32/` |

All eight artefacts of this work item are now tracked (committed by the Lead in
`08888e1`): the four `docs/v3.2/qa/*.md` files, the three
`fixtures/legacy_sessions/*.json` documents and
`tests/test_v32_legacy_fixtures.py`. Verified with
`git ls-files docs/v3.2/qa fixtures/legacy_sessions tests/test_v32_legacy_fixtures.py`.

`web/src/main.js` is modified in the working tree by another v3.2 work item
(it is outside this work item's write scope and was not touched here); it is
listed so that a later reader does not attribute it to the M0 baseline.


Version identity: `pyproject.toml:8` `version = "0.9.14"`, and
`git show HEAD:litsearch/version.py` contains `__version__ = "0.9.14"`.
`pip list` reports `litsearch 0.9.14` from the repo directory (editable install).
`web/package.json:3` still says `"version": "0.9.11"` while
`web/package-lock.json` (`lockfileVersion: 3`, root package version) also says
`0.9.11` — the Web bundle's own version field was never bumped with the project
(`pyproject.toml:8` = 0.9.14). Recorded as a discrepancy, not changed here.

## 2. Language runtimes

| Runtime | Value | Command |
|---|---|---|
| Python | `3.13.1 (tags/v3.13.1:0671451, Dec  3 2024, 19:06:28) [MSC v.1942 64 bit (AMD64)]` | `python --version`, `sys.version` |
| Python executable | `C:\Users\<user>\AppData\Local\Programs\Python\Python313\python.exe` (user-profile segment redacted) | `sys.executable` |
| Platform | `Windows-11-10.0.22631-SP0` | `platform.platform()` |
| Node | `v24.13.0` | `node --version` |
| npm | `11.6.2` | `npm --version` |
| virtualenv | **none** | `Test-Path .venv` → `False`; `$env:VIRTUAL_ENV` empty |

`Get-Command python` resolves to the Windows Store shim
(`C:\Users\<user>\AppData\Local\Microsoft\WindowsApps\python.exe`; profile segment
redacted), which forwards to
the real interpreter above. The repository's own `.venv` does **not** exist in
this checkout, so the `启动Web版.bat` / `启动Streamlit版.bat` path (which creates
`.venv` and installs into it) is not the path the baseline was measured through.

## 3. Dependency lock and installed versions

Two lock inputs exist:
* `constraints.txt` — 20 lines including 4 comment lines, **15 `==` pins**
  (`(Get-Content constraints.txt | Where-Object { $_ -match '==' }).Count`).
* `web/package-lock.json` — npm `lockfileVersion: 3`.
* `requirements.txt` also exists at the repo root.

Installed vs pinned (`python -m pip list` filtered to the pinned names;
`constraints.txt:5-20`):

| Package | Installed | Pin in `constraints.txt` | Match |
|---|---|---|---|
| requests | 2.32.5 | `requests==2.32.5` (line 5) | yes |
| streamlit | 1.54.0 | line 6 | yes |
| scikit-learn | 1.8.0 | line 7 (`python_version >= "3.11"`; Python is 3.13.1) | yes |
| fastmcp | 3.2.4 | line 9 | yes |
| python-dotenv | 1.2.2 | line 10 | yes |
| networkx | 3.6.1 | line 11 (`python_version >= "3.11"`) | yes |
| pytest | 9.0.3 | line 13 | yes |
| fastapi | 0.143.0 | line 14 | yes |
| uvicorn | 0.54.0 | line 15 | yes |
| httpx | 0.28.1 | line 16 | yes |
| ruff | 0.16.10 | line 17 | yes |
| fastembed | 0.8.0 | line 19 | yes |
| sentencepiece | 0.2.1 | line 20 | yes |
| litsearch | 0.9.14 (editable, `E:\DOCTOR 'S work content\LEExtractor_v0.9.14_work`) | `pyproject.toml:8` | yes |

Installed but **not pinned** in `constraints.txt`: `numpy 2.4.2`,
`pydantic 2.13.4`, and `PyMuPDF 1.27.2.3`.

> **Discrepancy worth recording:** `task-2`'s brief says PyMuPDF is "already
> installed and pinned in constraints.txt". It *is* installed (1.27.2.3) but
> **it is not pinned** — `constraints.txt` has no PyMuPDF or pypdf line
> (all 15 pins are listed above). Marked as evidence, not fixed here.

## 4. Startup commands (read from source, not guessed)

### 4.1 FastAPI app (the integrated Web workspace)

| Entry | Source | Command |
|---|---|---|
| Module entry | `litsearch/web/__main__.py:1-4` → `litsearch.web.api.main` | `python -m litsearch.web` |
| `main()` | `litsearch/web/api.py:859-878`; port default `8000` (`api.py:867`, overridable by env `LEEXTRACTOR_PORT`, `api.py:869`), host default `127.0.0.1` (env `LEEXTRACTOR_HOST`, `api.py:877`), `--data-dir` (env `LEEXTRACTOR_DATA_DIR`, `api.py:873`) | `python -m litsearch.web --port 8000` |
| Launcher (checks the built bundle, polls `/api/health`, opens a browser) | `scripts/launch_web.py:51-101`; default port `8000` (`scripts/launch_web.py:53`); refuses to start without `web/dist/index.html` (`:63-65`) | `python scripts/launch_web.py --no-browser --port 8000` |
| Windows launcher, creates `.venv` + `pip install -e ".[web]" -c constraints.txt` | `启动Web版.bat` (14 lines) | `启动Web版.bat` |
| Default double-click entry, delegates to the above | `启动LEExtractor.bat` (`call "%~dp0启动Web版.bat" %*`) | `启动LEExtractor.bat` |

Default data directory when nothing is set: `<repo>/.web-data`
(`api.py:201-202`); existing settings are re-hydrated from
`.web-data/settings.json` into `os.environ` at start-up (`api.py:205-212`).

### 4.2 Streamlit app (historical GUI)

| Entry | Source | Command |
|---|---|---|
| Launcher | `scripts/launch_gui.py:92-140`; app file `app.py` (`:23`), default port `8501` (`:24`), health probe `/_stcore/health` (`:108`) | `python scripts/launch_gui.py --no-browser` |
| Underlying process it spawns | `scripts/launch_gui.py:82-89` | `python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false` |
| Windows launcher | `启动Streamlit版.bat` | `启动Streamlit版.bat` |

### 4.3 MCP server

`litsearch/server.py:24-28` documents all four supported entry points, and
`pyproject.toml:27` declares the console script:

* `python server.py` (root shim)
* `python -m litsearch.server` (`litsearch/server.py:609-610`)
* `litsearch-mcp` (`pyproject.toml:27` → `litsearch.server:main`;
  `main()` = `mcp.run()`, `litsearch/server.py:605-606`)
* `fastmcp run litsearch/server.py`

It is a stdio MCP server exposing 12 tools; `mcp = FastMCP("LEExtractor —
Literature Review", ...)` at `litsearch/server.py:47-54`. **It does not start in
this environment** — see §6.

## 5. Test entry point and the recorded baseline result

Test configuration lives in `pyproject.toml:62-64`:
`testpaths = ["tests"]`, `filterwarnings = ["ignore::DeprecationWarning"]`.
No `pytest.ini`, `tox.ini` or `setup.cfg` test section overrides it.

**Exact command run** (from the repo root, `PYTHONUTF8=1` to make the Chinese
error text and file reads deterministic):

```
$env:PYTHONUTF8="1"; python -m pytest -q -p no:cacheprovider --tb=no -rf
```

**Recorded result (exit code 1):**

```
5 failed, 885 passed, 2 warnings in 256.88s (0:04:16)
```

The two warnings are `sklearn` `ConvergenceWarning`s from
`tests/test_landscape.py::test_cluster_count_is_capped` and
`tests/test_landscape.py::test_coverage_flags_thin_topics`
(“Number of distinct clusters … smaller than n_clusters”), not failures.

The Lead's earlier measurement recorded in the `task-5` brief was
`873 passed / 5 failed`. **The +12 passing difference is NOT VERIFIED**: the
working tree was being written by the other v3.2 work items while this ran
(§1 shows untracked `tests/test_v32_*.py` modules appearing between two
`git status` calls), and the exact composition of the Lead's earlier run cannot
be reconstructed from my data. What *is* the same in both measurements is the
failure set — the same 5 test ids below.

Collection counts of the untracked v3.2 modules at the time of writing
(`python -m pytest --collect-only -q <files>`):
`test_v32_contracts.py` 12, `test_v32_store.py` 15,
`test_v32_legacy_fixtures.py` 16 (this work item),
all five modules together 82.

### 5.1 The 5 pre-existing failures, by name and cause

Reproduced with:

```
python -m pytest tests/test_mcp_host_compat.py \
  tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release \
  tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip \
  -q -p no:cacheprovider --tb=line
# → 5 failed, 8 passed in 194.34s (0:03:14)
```

**These were not fixed** (out of scope for M0 and for this work item).

| # | Test id | Failure line | Cause (evidence) |
|---|---|---|---|
| 1 | `tests/test_mcp_host_compat.py::test_the_server_completes_a_real_mcp_handshake_over_stdio` | `test_mcp_host_compat.py:201` `assert 1 in {}` | environment — see below |
| 2 | `tests/test_mcp_host_compat.py::test_the_server_lists_tool_schemas_a_host_can_render` | `test_mcp_host_compat.py:224` `KeyError: 2` | environment — see below |
| 3 | `tests/test_mcp_host_compat.py::test_a_network_free_tool_call_succeeds_over_stdio` | `test_mcp_host_compat.py:249` `assert 2 in {}` | environment — see below |
| 4 | `tests/test_release_self_contained.py::test_every_document_reference_resolves_inside_the_release` | `test_release_self_contained.py:112` | packaging: “these documents are shipped but cite files the package omits: README.md -> ['DEPLOYMENT.md', 'THIRD_PARTY_NOTICES.md']” — the release package ships `README.md` but omits the two documents it links (both exist in the repo root, so the defect is in the release manifest, not in the links) |
| 5 | `tests/test_web_release.py::test_root_documents_and_web_guide_links_resolve_in_source_zip` | `test_web_release.py:133` | packaging: “unresolvable local Markdown links” inside the source zip — from `README.md`: `LICENSE`, `docs/demo/leextractor-demo.gif`, `docs/demo/leextractor-demo-small.gif`, `download/LEExtractor_v0.9.14_Windows_portable.zip`, `DEPLOYMENT.md`, `docs/devlog/`, `THIRD_PARTY_NOTICES.md`; plus `README.zh-CN.md -> README.en.md`. The test verifies link targets **inside the built source zip**, so the omitted paths are the cause |

#### Cause of failures 1–3 (measured, not guessed)

The three MCP tests spawn `python server.py` with piped stdio
(`tests/test_mcp_host_compat.py:150-157`) and assert a JSON-RPC reply arrives.
No reply ever does (`replies == {}`).

Reproduced directly — a manual spawn of `python server.py` with an `initialize`
line produced **0 stdout lines and 84 stderr lines**, the last of which is:

```
httpx.InvalidURL: Invalid port: ':1]'
  httpx/_client.py:698 in __init__  →  URLPattern(key)  →  httpx/_urlparse.py:411 normalize_port
```

The value that triggers it is the ambient proxy environment. Measured:

```
NO_PROXY = <internal-ip>,.local,localhost,127.0.0.1,::1,[::1]   (length 50; the internal IP is redacted here)
```

`httpx.Client(trust_env=True)` parses each `NO_PROXY` entry as a `URLPattern`,
and the literal token `[::1]` yields the bogus port `':1]'`. Minimal repro
(verified):

| `os.environ["NO_PROXY"]` | `httpx.Client(trust_env=True)` |
|---|---|
| `[::1]` | `httpx.InvalidURL: Invalid port: ':1]'` |
| `::1` | OK |
| `localhost` | OK |

fastmcp constructs an httpx client during `mcp.run()`, so the server process dies
before it can answer anything. **Decisive experiment:** spawning the same
`server.py` with the proxy variables removed from the child environment
(`NO_PROXY=localhost,127.0.0.1`, no `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`)
makes it answer: 2 stdout lines, `initialize` → `serverInfo.name =
"LEExtractor — Literature Review"`, `tools/list` → **12 tools**. So failures 1–3
are an environment/host-proxy artefact of this machine, not product defects, and
they are pre-existing with respect to the v3.2 work.

**NOT VERIFIED:** setting `$env:NO_PROXY` at the PowerShell level did **not**
change the child's value (a re-measurement inside the same command still showed
`[::1]`, length 50), so the 3 failures could not be removed from this shell
without editing the test or the product. They are left failing on purpose.

## 6. Other environment quirks observed

* `Get-ChildItem env:` fails in this shell with
  `An item with the same key has already been added.` — the process environment
  contains duplicate names differing only in case (Windows environment blocks are
  case-insensitive, and the .NET provider rejects the duplicates). This is why
  §4/§6 read environment variables through Python (`os.environ`) instead.
* A demo project cannot run a search job: `POST
  /api/projects/{pid}/search` on a demo project returns **409
  `demo_search_disabled`** (“演示项目只运行本地模拟扩展；请新建真实检索项目”).
  Demo projects only exercise local synthetic expansion.
* Local semantic ranking needs a **252 MB** local embedding model that is not on
  this machine; the failure is `local_model_failure` and the message states the
  model could not be located locally and its download needs the internet. No
  network egress is available here.

## 7. M0 timing requirement — small real retrieval + fixed-corpus replay

`docs/v3.2/06_REUSE_CHANGE_MAP.md:32` requires one small real retrieval and one
fixed-corpus replay to determine per-stage ranking timings.

### 7.1 Small-scale real retrieval — **external_blocked / NOT VERIFIED**

No live retrieval was run. Reason: it needs outbound network access to the
literature providers, and this machine has no egress for that (the same
condition that stops the 252 MB embedding-model download, §6). No provider
credential is present in the environment either (`api.py:184-190` reads
`S2_API_KEY`, `OPENALEX_API_KEY`, `SERPAPI_API_KEY`,
`LEEXTRACTOR_UNPAYWALL_EMAIL`; none is inspected or recorded here). **No timing
number is reported for this item** rather than inventing one.

### 7.2 Fixed-corpus replay — measured, partially

Two network-free replays were run against the real app with a deterministic
in-process `DemoSource` (`litsearch/demo.py:30-53`) injected through
`create_app(workflow_factory=...)` (`litsearch/web/api.py:193`, `214-218`), with
3 repetitions each. Harness: `fastapi.testclient.TestClient`, wall clock
`time.perf_counter()`.

**(a) Demo-corpus ranking** — `POST /api/demo/projects`
(`api.py:461-468`) builds the fixed 8-record corpus and re-ranks it
(`litsearch.search ... Re-ranked 8 papers for one score context, stage=synthetic_demo`):

| Run | demo project create (s) | records |
|---|---|---|
| 1 | 0.0544 | 8 |
| 2 | 0.0461 | 8 |
| 3 | 0.0377 | 8 |

**(b) Fixed-corpus citation expansion** on a project imported from the M0
fixture `fixtures/legacy_sessions/current_v4_full.json` (3 corpus records),
`POST /api/projects/{pid}/expand` with one seed, `max_rounds=1`,
`max_per_direction=5`, `http_budget=50`:

| Run | submit (s) | job status | stop_reason | job wall (s) | corpus after |
|---|---|---|---|---|---|
| 1 | 0.0397 | completed | `no_new_results` | 0.1285 | 3 |
| 2 | 0.0415 | completed | `no_new_results` | 0.1293 | 3 |
| 3 | 0.0382 | completed | `no_new_results` | 0.1367 | 3 |

The same expand request with the session's **default** ranking mode (`semantic`)
did not complete: status `failed`, `stop_reason=local_model_failure`,
`job wall ≈ 21–25 s` (dominated by the failed model lookup), `error.code =
local_model_failure`, message “本地 Embedding 模型加载失败 … [local file] 252 MB”.
The lexical mode above was reached by setting the imported session's
`search_manifest.ranking_mode` to `"lexical"` before import.

**Per-stage ranking timings: NOT VERIFIED.** The completed lexical jobs reported
`timings = {"total": 12.5}` for a job whose measured wall time was ~0.13 s. 12.5 is
exactly the `search_manifest.timings.total` carried in the imported fixture, so
that number is inherited session metadata, not a fresh per-stage measurement;
no `ranking`/`normalization`/`provider_total` stage keys were produced at all.
Reading the stages from this route would therefore be wrong, and the semantic
route — the one that actually exercises the ranking stages — cannot run here
because the embedding model is absent and its download needs the network.
Recording the number would have violated the M0 rule against presenting
inherited or unmeasured numbers as measurements.

**NOT VERIFIED:** the per-stage cost of each ranking stage
(`query_planning`, `translation`, `embedding_load`, `provider_total`,
`normalization`, `ranking`, `persistence` — the stage names the Web UI labels at
`web/src/App.vue:131`) could not be measured on this machine for the reasons
above.

## 8. Commands used to build this document

```
git rev-parse --abbrev-ref HEAD
git log -1 --format=%H%n%ad%n%s
git status --porcelain
git diff --stat ; git diff -- pyproject.toml
git show HEAD:litsearch/version.py
python --version ; python -c "import sys,platform;print(sys.executable);print(sys.version);print(platform.platform())"
node --version ; npm --version
python -m pip list
python -m pytest -q -p no:cacheprovider --tb=no -rf
python -m pytest -q -p no:cacheprovider --tb=line <the 5 failing ids>
```
