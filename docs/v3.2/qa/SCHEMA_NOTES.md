# M0 Schema Notes — the real legacy session schema and what a v3.2 migration must preserve

**Baseline:** branch `feature/v3.2`, commit `e601861bde58b641a1161369a7b5c037b59955c1`.
**Why this file exists:** `docs/v3.2/06_REUSE_CHANGE_MAP.md:30` requires M0 to
record the session schema, and `docs/v3.2/14_JOBS_IDENTITY_MIGRATION.md` forbids
treating the document version "3.2" as a legacy integer schema plus one. This
document records the versions that actually exist in code, so the migration is
built from measurements rather than assumptions.

---

## 1. There are three independent version namespaces — do not conflate them

| Namespace | Constant / field | Value | Citation |
|---|---|---|---|
| Legacy session document | `SCHEMA_VERSION` (written as the JSON key `version`) | **4** | `litsearch/persistence.py:47`, `:153` |
| Legacy session validator | `SESSION_SCHEMA_VERSION` | **4** | `litsearch/session_schema.py:50` |
| Legacy session versions readable | `SUPPORTED_PAST_VERSIONS` | **(1, 2, 3)** | `litsearch/session_schema.py:53` |
| v3.2 project document | `SCHEMA_VERSION`, key `schema_version` (integer) | **1** | `litsearch/v32/store.py:71`, `:369` |
| v3.2 project contract | `CONTRACT_VERSION`, key `contract_version` (string) | **"3.2"** | `litsearch/v32/store.py:74`, `:370` |
| v3.2 legacy-session versions readable *by the new store* | `SUPPORTED_LEGACY_SCHEMA_VERSIONS` | **(0,)** | `litsearch/v32/store.py:80` |
| Contracts pack | `defaults.json` `"schema_version"` | **"3.2"** (a string) | `litsearch/contracts/defaults.json:93` |
| AI rerank contract | `ai_rerank.schema.json` `schema_version.const` | **"3.2"** | `litsearch/contracts/ai_rerank.schema.json:5`, asserted by `litsearch/v32/contracts.py:374-376` |
| Search cache | `CACHE_SCHEMA_VERSION` | **3** | `litsearch/cache.py:8` |
| Export bundle | `BUNDLE_SCHEMA_VERSION` | **1** | `litsearch/version.py:20` |

The new store says this explicitly and refuses to guess:
`litsearch/v32/store.py:68-71` — *“Deliberately a small integer and deliberately
not the document version "3.2" (doc 14: "不能将文档版本 3.2 直接当成旧整数
schema+1")”*.

> **Note on line numbers in `litsearch/v32/store.py`:** that file is untracked and
> was being written by another v3.2 work item while this document was produced;
> its line numbers moved between two reads minutes apart (`raw.get("schema_version")`
> was at line 224, then at line 278). Citations to it are as of the measurement
> time and should be re-verified against the final commit.

## 2. The real legacy session versions

`version` is an **integer** in the JSON document.

* **v1 — no `version` key at all.** `schema_version_of()` returns `1` when the key
  is missing (`litsearch/session_schema.py:687-698`), and the migrator records the
  warning `legacy_without_version` (`:715-717`).
* **v2, v3, v4 — declared** as `{"version": 2|3|4}`.
* **Anything else is refused, not downgraded:** non-int / `bool` / `< 1` produces
  the `invalid_version` error (`:695-697`, `:729-735`); `version > 4` produces
  `future_version` and the document is rejected (`:736-741`). The module docstring
  states the rule at `:22-23`: *“A future schema version is rejected, not
  downgraded: silently dropping fields a newer build wrote is how a user loses
  work.”*

Migration chain (`litsearch/session_schema.py:684`):
`_MIGRATIONS = {1: _migrate_v1_to_v2, 2: _migrate_v2_to_v3, 3: _migrate_v3_to_v4}`,
applied in `migrate_session_dict` (`:701-721`), which finally stamps
`migrated["version"] = 4` (`:720`).

| Step | Function | What it fills (citation) |
|---|---|---|
| 1 → 2 | `_migrate_v1_to_v2` (`:586-594`) | normalises *object-shaped* `similar_papers` entries into `[paper, score, method]` triples (`:563-572`), object-shaped `key_journals` / `key_authors` into `[name, count]` pairs (`:575-583`), and defaults `search_manifest={}`, `saved_at=0.0`, `start_time=0.0`, `topic_clusters={}`, `year_distribution={}` |
| 2 → 3 | `_migrate_v2_to_v3` (`:597-645`) | per PRISMA record adds `full_text_retrieved=False`, `full_text_decision="pending"`, `full_text_reason=""`, `screening_reason=""`, `relevance_score=0.0`, `source=""`; per snowball round adds `round_number`, `direction="both"`, `raw_count`, `unique_count`, `relevant_count=0`, `cumulative_unique=0`, `source_papers=[]`; per paper runs `_migrate_paper` (`:648-658`) adding the identifier/discovery blocks |
| 3 → 4 | `_migrate_v3_to_v4` (`:658-681`) | sets `calibration=None` when absent; per PRISMA record adds `score_context_id=""`, `requires_manual_review=False`, `conflicts=[]`, `history=[]` |

Every filled value is recorded as a warning entry
`{"code": "migrated", "field": ..., "message": ...}`; “an unknown value fails
validation instead of being invented” (`:30-31`).

## 3. Shape of `litsearch/session_schema.py` (938 lines)

* **Entry contract** (`:8-20`): `SESSION_SCHEMA_VERSION`,
  `validate_session_dict` (validate **and** migrate; returns a new dict at the
  current version), `migrate_session_dict` (migration only),
  `load_session_file`, `backup_corrupt_session`, and `SessionSchemaError` whose
  `str()` always starts with `ERROR_PREFIX = "SESSION_SCHEMA_INVALID"`
  (`:56`). `SessionSchemaError.errors` is a list of
  `{"field", "code", "message"}` (`:87-104`).
* **Hard limits** (`:58-67`): `MAX_PAPERS = 50_000`, `MAX_RECORDS = 50_000`,
  `MAX_TEXT = 4096`, `MAX_HISTORY = 10_000`, `MAX_METADATA_ITEMS = 100_000`,
  `MAX_METADATA_DEPTH = 16`, `MIN_YEAR = 1000`, `MAX_YEAR = 2200`.
* **Enums** (`:69-71`): phases `("scoping", "systematic_search", "snowballing",
  "screening", "complete")` (fallback list; the real set is read from
  `ReviewPhase` when importable, `:74-81`), decisions
  `("pending","accept","reject","maybe")`, calibration statuses
  `("calibrated","insufficient_labels")`.
* **Validation order** (`validate_session_dict`, `:745-770`): document must be a
  dict → version check → `_validate_state_extensions` → migrate → `_validate_document`.
  Errors are **all** collected and raised together.
* **What is validated, field by field** (`_validate_document`, `:500-555`):
  `topic`, `research_direction` (text), `phase` (enum), `saved_at`/`start_time`
  (finite number; `saved_at` non-negative), `scoping_papers`/`search_papers`
  (list of paper objects), `similar_papers` (list of `[paper, score, method]`),
  `key_journals`/`key_authors` (list of `[name, int]` pairs),
  `topic_clusters` (dict of string lists), `year_distribution` (dict keyed by a
  digit string, non-negative int values), `search_manifest` (`ranking_mode` ∈
  {lexical, semantic}; `query_context` sub-keys text), `calibration`, `prisma`,
  `snowball_result`, plus the optional v4 runtime fields in
  `_validate_state_extensions` (`:233-264`).
  **Absent keys are simply not validated** — the schema is intentionally
  permissive about optional history, which is why all three M0 fixtures load.
* **Paper validation** (`_validate_paper`, `:267-318`): `id` non-empty,
  `canonical_id`/`title`/`abstract`/`publication_status`/`search_snippet` text,
  `year` int within 1000–2200, `citation_count`/`reference_count` int ≥ 0,
  `relevance_score` finite number, `score_context_id` text,
  `citation_ids`/`reference_ids`/`topics` string lists, `authors` list of objects
  with a `name`, `identifiers` (text values), `score_breakdown` (numeric values),
  `discovery_traces` list of objects with a `method`.
* **File access** (`load_session_file`, `:795-819`): unreadable → `unreadable`
  error; invalid JSON → file backed up (`invalid_json`); schema-invalid → file
  backed up (`schema_invalid`). “the original is never modified”
  (`:798-800`). `backup_corrupt_session` (`:778-792`) writes
  `<name>.corrupt-<utc>.bak` and never overwrites an existing backup.
* **Project isolation** (`:822-875`): `project_id_for(topic, direction)` =
  first 12 hex chars of `sha256(topic + "\n" + direction)` (`:834-846`);
  `sanitize_project_id` keeps `[A-Za-z0-9._-]`, max 64 chars, strips leading
  `-.` (`:849-856`); `project_session_path` puts each project under
  `<session_dir>/projects/<id>/autosave.json` (`:859-875`).
* **Write-conflict helpers** (`:878-938`): `_session_header`,
  `detect_write_conflict`, `conflict_copy_path` — the v0.9.x mechanism that stops
  two browser tabs from silently overwriting one autosave.

## 4. Shape of `litsearch/persistence.py` (493 lines)

* `SESSION_DIR = os.path.join(BASE_DIR, ".sessions")`, `AUTOSAVE_NAME = "autosave.json"`
  (`:41-42`); `SCHEMA_VERSION = 4` (`:47`).
* **Paper ↔ dict**: `paper_to_dict` (`:55-79`) and `paper_from_dict` (`:82-106`).
  `paper_from_dict` only constructs dataclasses with `**` unpacking for
  `discovery_traces` (`:102`), so a fixture discovery-trace object with an unknown
  key would raise — the M0 fixtures use exactly the `DiscoveryTrace` fields
  (`litsearch/models.py:10-18`: `method`, `provider`, `query`, `seed_id`,
  `round_no`, `score`, `evidence_ids`, `timestamp`).
  Unknown `identifiers` keys are filtered against
  `PaperIdentifiers.__dataclass_fields__` (`:101`), so that block is tolerant.
* **State → dict** (`state_to_dict`, `:114-185`) emits, in order:
  `version`, `saved_at`, `topic`, `research_direction`, `phase`, `start_time`,
  `scoping_papers`, `topic_clusters`, `key_journals`, `key_authors`,
  `year_distribution`, `search_papers`, `similar_papers`, `snowball_result`,
  `prisma` (PRISMA tracker dict **plus** `prisma["papers"]` re-attached from
  `state.prisma.records`, `:115-118`), `search_manifest`, `stop_reason`,
  `http_budget`, `run_history`, `score_context_id`, `score_context_stage`,
  `score_context_history`, `calibration_context_id`, `calibration`.
* **State ← dict** (`state_from_dict`, `:239-369`): the **single restore path**
  for every importer. It calls `validate_session_dict` first (`:243-245`) with the
  comment that “Every import route (including Web and MCP callers) shares the same
  migration/version checks as a disk restore. Never silently downgrade a future
  session merely because the caller already decoded its JSON.” Then it rebuilds
  `ReviewState`, `SnowballResult`/`SnowballRound`, and the PRISMA tracker
  (records whose `key` has no paper in `prisma.papers` are **skipped**, `:315-319`),
  re-registers identifier aliases (`:340-342`), restores the count ledger
  (`:343-345`), recovers a single scoring context when the document predates
  `score_context_id` (`:348-355`), and attaches the calibration through
  `load_calibration` (`:356`).
* **Calibration invalidation** (`load_calibration`, `:188-236`): a restored
  calibration is kept but marked invalid when the algorithm/score version moved,
  the `corpus_hash` no longer matches, the query changed, or the scoring context
  changed. `CorrelationRecord.usable` / `invalidated_by` live in
  `litsearch/filters.py:142-192`; `ALGORITHM_VERSION = "lexical_v3_word_char_coverage"`
  and `SCORE_VERSION = "hybrid_lexical_v1"` at `litsearch/filters.py:47`, `:50`.
* **Autosave**: `save_state` writes a temp file with `ensure_ascii=False` and
  `os.replace`s it atomically (`:387-401`); `load_state` (`:404-446`) validates
  before restoring and refuses to modify a rejected file.

## 5. The M0 legacy fixtures (frozen starting point)

Three documents in `fixtures/legacy_sessions/`, all synthetic (placeholder authors,
`10.0000/synthetic.*` identifiers, no personal data, no credential):

| File | Declared `version` | Shape it freezes |
|---|---|---|
| `legacy_v1_no_version.json` | key absent (v1) | object-shaped `similar_papers` / `key_journals` / `key_authors`, no `prisma`, no `snowball_result`, no `search_manifest`, no `calibration` |
| `legacy_v2_prisma_snowball.json` | `2` | tuple-shaped pairs, `prisma.records` without the v3 fields, `snowball_result.rounds` without `round_number`/counters, `search_manifest` present |
| `current_v4_full.json` | `4` | the full current shape: `prisma` with `ledger`/`conflicts`/`history`/`retrieval_attempted`, `snowball_result` complete, `stop_reason`, `http_budget`, `run_history`, `score_context_id`/`_stage`/`_history`, `calibration_context_id`, a `calibration` record, `similar_papers` triple, `search_manifest` with `query_context` |

Measured acceptance (implemented by `tests/test_v32_legacy_fixtures.py`):

```
python -m pytest tests/test_v32_legacy_fixtures.py -q
16 passed
```

Loader output actually observed for each file, through
`validate_session_dict` + `state_from_dict`:

| File | declared | migrated to | phase | search_papers | prisma records | migration warnings |
|---|---|---|---|---|---|---|
| `legacy_v1_no_version.json` | `None` | 4 | `systematic_search` | 1 | 0 | 14 |
| `legacy_v2_prisma_snowball.json` | 2 | 4 | `snowballing` | 2 | 2 | 29 |
| `current_v4_full.json` | 4 | 4 | `screening` | 2 | 2 | 0 (a current document must need no migration) |

## 6. What a legacy → v3.2 migration must preserve

Derived from the fields the legacy writer emits (`persistence.py:152-185`) and
the fields the v3.2 project document carries — the document dict built by
`create_project` begins at `litsearch/v32/store.py:368` with `schema_version`,
`contract_version`, `project_id`, `owner_id`, `name`, `entry_mode`, `revision`,
`context_version`, `corpus_version`, `model_revision`, `current_snapshot_id`
(the literal continues past line 379), and the store's referential-integrity
check (`store.py:329-349`) also requires `papers`, `aliases`, `judgements`,
`edges`, `seeds` and `snapshots` to be mutually consistent.

**Must survive verbatim (loss is data loss, not a formatting change):**

1. **Paper identity and external keys** — `id`, `canonical_id`, `doi`,
   `identifiers{doi, semantic_scholar_id, openalex_id, arxiv_id, pmid,
   openreview_id, dblp_key, google_scholar_id}`, and the alias set built by
   `paper_aliases` (used at `persistence.py:340-342`). The v3.2 store validates
   that every alias and every reference resolves
   (`litsearch/v32/store.py:329-349`), so a dropped alias becomes a dangling
   reference and the migration is refused.
2. **Every human judgement and its audit trail** — `prisma.records[].key`,
   `screening_decision`, `screening_reason`, `full_text_retrieved`,
   `full_text_decision`, `full_text_reason`, `retrieval_attempted`,
   `retrieval_failure_reason`, `relevance_score`, `score_context_id`,
   `requires_manual_review`, `conflicts[]`, `history[]`
   (`litsearch/prisma.py:1108-1126`). The distinction between “nobody tried to
   fetch this” and “we tried and failed” (`prisma.py:206-212`) must not be
   flattened.
3. **The count ledger and PRISMA identification counts** —
   `prisma.ledger`, `prisma.identification_counts`, `prisma.duplicates_seen`,
   `prisma.full_text_stage_enabled` (`persistence.py:305-346`). PRISMA numbers
   are reported facts and are not recomputable from the corpus.
4. **The exact `relevance_score` semantics and its scoring context** —
   `score_context_id`, `score_context_stage`, `score_context_history`,
   `calibration_context_id`, and the calibration record with
   `algorithm_version` / `score_version` / `corpus_hash` / `threshold` /
   `evaluation` (`persistence.py:174-184`, `:188-236`). A migrated score must
   never be silently re-normalised into a new context, and a stale calibration
   must be carried over **marked invalid**, not deleted.
5. **Discovery provenance** — per-paper `discovery_traces` and
   `search_manifest` (provider counts, diagnostics, `timings`, `query_context`
   with `original`/`translated`/`topic`/`direction`/`engine`/`revision`).
   `docs/v3.2/08_API_DATA_MODEL_SPEC.md:15` requires the original user input to
   remain un-overwritten, so the raw `topic` / `research_direction` must be kept
   alongside any generated query variants.
6. **Run/stop accounting** — `run_history`, `stop_reason`, `http_budget`
   (including `by_source`), `start_time`, `saved_at`. Verified round-trip in
   `tests/test_web_persistence.py:43-66`.
7. **Corpus membership, not just the “relevant” subset** — `scoping_papers` and
   `search_papers` in full, plus `similar_papers`, `snowball_result.all_papers`
   and `snowball_result.rounds[]` with `seed_ids` / `pending_seeds` / `canceled` /
   `failed_seeds` (`persistence.py:120-150`, `:284-303`). `docs/v3.2/05`-level
   ordering may change; membership may not be dropped.
8. **Empty structures stay present as empty arrays/objects** —
   `docs/v3.2/08_API_DATA_MODEL_SPEC.md:67`. The legacy writer already emits
   `"scoping_papers": []`, `"similar_papers": []`, `"snowball_result": null`
   rather than omitting them (`persistence.py:159-168`); the migration must not
   convert “empty” into “absent”, because absence is read as “unknown”.
9. **Nothing may be invented.** The legacy migrator's own rule —
   “an unknown value fails validation instead of being invented”
   (`session_schema.py:30-31`) — carries over: a legacy field the v3.2 shape has
   no place for must not be guessed, and a missing number must stay `null`, never
   become `0` (`docs/v3.2/08_API_DATA_MODEL_SPEC.md:61`).
10. **New v3.2-only fields must be nullable/defaulted, not fabricated** — in
    particular `owner_id` (the legacy document has no owner; v3.2 adds one at
    `store.py:372`) and the snapshot/ranking fields. `docs/v3.2/08_API_DATA_MODEL_SPEC.md:61`
    states the rule: “空的新字段为 null，不以 0 冒充未知”.
11. **The old path stays readable** — `docs/v3.2/08_API_DATA_MODEL_SPEC.md:61`:
    `paper_key` remains a legacy alias resolved to `paper_id` at the API
    boundary, and “移除旧 alias 前必须有弃用与迁移记录”. The existing
    `POST /api/projects/import` route (`litsearch/web/api.py:448-459`), which
    funnels through `state_from_dict` + `validate_session_dict`, is the
    compatibility surface the fixtures pin down.

## 7. Items **NOT VERIFIED** in this document

1. **The v3.2 project-document line numbers in `litsearch/v32/store.py`** — that
   untracked module was being edited concurrently; its line numbers moved during
   measurement (§1 note). Re-verify against the final commit.
2. **Whether the v3.2 migration chain `_MIGRATIONS[0]` actually consumes a legacy
   session `version` document** (rather than only the `schema_version`-less
   v3.2-project shape) — the store keys off `schema_version`
   (`store.py:278-281`), while the legacy session carries `version`. The two
   namespaces are distinct in code and no adapter between them is present in this
   working tree; whether one is planned is not stated in the files read here.
3. **`.sessions/autosave.json` (the one real session file in the checkout) was not
   inspected** — it is user data; its `version` and contents are not recorded in
   any artefact of this work item.
