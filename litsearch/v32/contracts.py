"""Single source of truth for the v3.2 machine contracts.

Every number, enum and schema the v3.2 layer obeys is read from the JSON files
in ``litsearch/contracts/`` rather than re-declared in Python.  The
specification is explicit that fields, enums and defaults are governed by
``contracts/`` (START_HERE.md, "规范优先级"), so a hard-coded ``k = 60``
anywhere else in the codebase is a defect, not a shortcut.

The files shipped in the package are the runtime contracts:

``defaults.json``        engineering defaults (weights, budgets, thresholds)
``job_states.json``      the job state machine and its terminal states
``access_links.json``    the fixed registry of official access entry points
``domain.schema.json``   JSON Schema for every domain object
``ai_rerank.schema.json``JSON Schema for structured AI rerank output
``openapi.json``         the incremental HTTP contract

Test-only fixtures (``fixtures/*.json``) are loaded through
:func:`fixture` and are not part of the installed package.
"""

from __future__ import annotations

import copy
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "ContractError",
    "CONTRACTS_DIR",
    "FIXTURES_DIR",
    "document_version",
    "algorithm_version",
    "defaults",
    "section",
    "ranking",
    "discovery",
    "pdf_defaults",
    "ai_defaults",
    "job_defaults",
    "mapping_defaults",
    "performance_targets",
    "credentials_defaults",
    "access_defaults",
    "zotero_defaults",
    "runtime_defaults",
    "weights",
    "job_states",
    "terminal_states",
    "initial_state",
    "can_transition",
    "assert_transition",
    "access_links",
    "access_link_registry",
    "domain_schema",
    "ai_rerank_schema",
    "openapi_schema",
    "fixture",
    "ranking_cases",
    "acceptance_cases",
    "job_event_cases",
    "ai_valid_fixture",
    "ai_semantic_cases",
    "validate_contracts",
    "reset_cache",
]


class ContractError(RuntimeError):
    """A contract file is missing, unreadable, or internally inconsistent."""


#: ``litsearch/contracts`` — resolved relative to this module so an editable
#: install, a wheel and a frozen build all find the same files.
CONTRACTS_DIR = Path(__file__).resolve().parent.parent / "contracts"


def _default_fixtures_dir() -> Path:
    override = os.environ.get("LEEXTRACTOR_FIXTURES_DIR")
    if override:
        return Path(override).expanduser()
    # litsearch/v32/contracts.py -> litsearch/v32 -> litsearch -> repo root
    return Path(__file__).resolve().parents[2] / "fixtures"


#: ``<repo root>/fixtures`` — test fixtures, overridable for tests that must
#: run from an installed wheel.
FIXTURES_DIR = _default_fixtures_dir()


def _read(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"contract file is not readable: {path}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ContractError(f"contract file is not valid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"contract file must contain a JSON object: {path}")
    return data


@lru_cache(maxsize=None)
def _cached(directory: str, name: str) -> dict[str, Any]:
    return _read(Path(directory), name)


def reset_cache() -> None:
    """Drop every cached contract.  Used by tests that patch the directories."""
    _cached.cache_clear()
    global FIXTURES_DIR
    FIXTURES_DIR = _default_fixtures_dir()


def _contract(name: str) -> dict[str, Any]:
    """A private copy of a cached contract, so callers cannot corrupt it."""
    return copy.deepcopy(_cached(str(CONTRACTS_DIR), name))


# --------------------------------------------------------------------------
# defaults.json
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _defaults_frozen() -> dict[str, Any]:
    return _cached(str(CONTRACTS_DIR), "defaults.json")


def defaults() -> dict[str, Any]:
    """The whole ``contracts/defaults.json`` document (a copy)."""
    return copy.deepcopy(_defaults_frozen())


def _frozen() -> dict[str, Any]:
    """Shared read-only view.  Callers must not mutate it."""
    return _defaults_frozen()


def document_version() -> str:
    return str(_frozen()["document_version"])


def algorithm_version() -> str:
    return str(_frozen()["algorithm_version"])


def section(name: str) -> dict[str, Any]:
    """One named section of ``defaults.json`` (a copy).

    Raises:
        KeyError: the section is not part of the contract.
    """
    if name not in _frozen():
        raise KeyError(f"unknown defaults section: {name!r}")
    return copy.deepcopy(_frozen()[name])


def ranking() -> dict[str, Any]:
    return section("ranking")


def discovery() -> dict[str, Any]:
    return section("discovery")


def pdf_defaults() -> dict[str, Any]:
    return section("pdf")


def ai_defaults() -> dict[str, Any]:
    return section("ai")


def job_defaults() -> dict[str, Any]:
    return section("jobs")


def mapping_defaults() -> dict[str, Any]:
    return section("mapping")


def performance_targets() -> dict[str, Any]:
    return section("performance_targets")


def credentials_defaults() -> dict[str, Any]:
    return section("credentials")


def access_defaults() -> dict[str, Any]:
    return section("access")


def zotero_defaults() -> dict[str, Any]:
    return section("zotero")


def runtime_defaults() -> dict[str, Any]:
    return section("runtime")


def weights() -> dict[str, float]:
    """The RRF signal weights, in contract order (topic, impact, graph, seed, feedback)."""
    raw = _frozen()["ranking"]["weights"]
    return {str(k): float(v) for k, v in raw.items()}


# --------------------------------------------------------------------------
# job_states.json
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _job_states_frozen() -> dict[str, Any]:
    return _cached(str(CONTRACTS_DIR), "job_states.json")


def job_states() -> dict[str, Any]:
    """The whole state machine document (a copy)."""
    return copy.deepcopy(_job_states_frozen())


def terminal_states() -> frozenset[str]:
    return frozenset(_job_states_frozen()["terminal"])


def initial_state() -> str:
    return str(_job_states_frozen()["initial"])


def can_transition(source: str, target: str) -> bool:
    """True when ``source -> target`` is a legal edge of the state machine."""
    transitions = _job_states_frozen()["transitions"]
    if source not in transitions:
        return False
    return target in transitions[source]


def assert_transition(source: str, target: str) -> None:
    """Raise :class:`ContractError` when the edge is not in the contract.

    Terminal states have no outgoing edges, which is how "终态不可改写"
    (terminal states are immutable) is enforced rather than re-implemented.
    """
    transitions = _job_states_frozen()["transitions"]
    if source not in transitions:
        raise ContractError(f"unknown job state: {source!r}")
    if target not in transitions:
        raise ContractError(f"unknown job state: {target!r}")
    if target not in transitions[source]:
        raise ContractError(f"illegal job transition: {source!r} -> {target!r}")


# --------------------------------------------------------------------------
# access_links.json
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _access_links_frozen() -> dict[str, Any]:
    return _cached(str(CONTRACTS_DIR), "access_links.json")


def access_links() -> dict[str, Any]:
    """The fixed registry of official access entry points (a copy)."""
    return copy.deepcopy(_access_links_frozen())


def access_link_registry() -> dict[str, dict[str, Any]]:
    """``{id: entry}`` over the registry, for direct lookup by entry id."""
    return {str(entry["id"]): copy.deepcopy(entry) for entry in _access_links_frozen()["links"]}


# --------------------------------------------------------------------------
# JSON Schemas
# --------------------------------------------------------------------------


def domain_schema() -> dict[str, Any]:
    return _contract("domain.schema.json")


def ai_rerank_schema() -> dict[str, Any]:
    return _contract("ai_rerank.schema.json")


def openapi_schema() -> dict[str, Any]:
    return _contract("openapi.json")


# --------------------------------------------------------------------------
# fixtures (test-only)
# --------------------------------------------------------------------------


def fixture(name: str) -> dict[str, Any]:
    """Load ``fixtures/<name>``.  ``name`` may omit the ``.json`` suffix."""
    if not name.endswith(".json"):
        name += ".json"
    return _read(FIXTURES_DIR, name)


def ranking_cases() -> list[dict[str, Any]]:
    return list(fixture("ranking_cases.json")["cases"])


def acceptance_cases() -> list[dict[str, Any]]:
    return list(fixture("acceptance_cases.json")["cases"])


def job_event_cases() -> dict[str, Any]:
    return fixture("job_event_cases.json")


def ai_valid_fixture() -> dict[str, Any]:
    return fixture("ai_valid.json")


def ai_semantic_cases() -> dict[str, Any]:
    return fixture("ai_semantic_cases.json")


# --------------------------------------------------------------------------
# self-check
# --------------------------------------------------------------------------


def validate_contracts() -> dict[str, Any]:
    """Load every contract and assert the cross-file invariants hold.

    Returns a small report instead of raising, so the release check can print
    what it verified.  Raises :class:`ContractError` on a broken contract.
    """
    base = _frozen()
    if base["document_version"] != "3.2":
        raise ContractError(f"unexpected document_version: {base['document_version']!r}")

    state_machine = _job_states_frozen()
    reachable = {state_machine["initial"]}
    frontier = [state_machine["initial"]]
    while frontier:
        current = frontier.pop()
        for target in state_machine["transitions"].get(current, []):
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    declared = set(state_machine["transitions"]) | set(state_machine["terminal"])
    if reachable != declared:
        raise ContractError(
            "unreachable job states: " + ", ".join(sorted(declared - reachable))
        )
    for state in state_machine["terminal"]:
        if state_machine["transitions"].get(state):
            raise ContractError(f"terminal state {state!r} has outgoing transitions")

    signal_total = round(sum(weights().values()), 12)
    if signal_total != 1.0:
        raise ContractError(f"RRF weights must sum to 1.0, got {signal_total}")

    for entry in _access_links_frozen()["links"]:
        if not entry["url"].startswith("https://"):
            raise ContractError(f"access link {entry['id']!r} is not https")
        if not entry.get("checked_at") and not _access_links_frozen().get("checked_at"):
            raise ContractError(f"access link {entry['id']!r} has no checked_at")

    ai_schema = _contract("ai_rerank.schema.json")
    if ai_schema["properties"]["schema_version"]["const"] != "3.2":
        raise ContractError("ai_rerank.schema.json schema_version const must be '3.2'")

    schema = _contract("domain.schema.json")
    definitions = schema["$defs"]
    for name, node in definitions.items():
        for field in node.get("required", []):
            if field not in node.get("properties", {}):
                raise ContractError(f"{name}.{field} is required but not declared")

    return {
        "document_version": base["document_version"],
        "algorithm_version": base["algorithm_version"],
        "job_states": len(state_machine["transitions"]),
        "domain_definitions": len(definitions),
        "access_links": len(_access_links_frozen()["links"]),
        "weights_sum": signal_total,
    }
