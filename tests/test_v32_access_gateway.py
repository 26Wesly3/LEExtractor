"""M5 access gateway: registry-only links, honest labels, no authentication code.

The whole point of these tests is what the module must *not* do: invent a URL,
guess a publisher deep link, or contain anything that talks to a campus
identity system (AC-23, AC-28 and doc 20's deferred boundary).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from litsearch.v32 import access_gateway as AG
from litsearch.v32 import contracts

V32_DIR = Path(contracts.__file__).resolve().parent

#: Artefacts that would mean this round crossed the deferred boundary.  The
#: CAS-specific ones are checked in every module of the package; the wider
#: ones only in the four modules this work item owns, so a peer's unrelated
#: wording cannot be mistaken for an authentication flow.
CAS_SOURCE_PATTERNS = (
    r"zjuam",
    r"cas/login",
    r"/cas/",
    r"serviceTicket",
    r"浙江大学账号登录",
    r"浙大统一",
)
OWNED_MODULES = ("credentials.py", "access_gateway.py", "venue_metrics.py", "zotero.py")
#: Form-shaped controls rather than the bare word: a redaction routine must be
#: able to *name* the fields it masks (credentials.py lists them in a regex),
#: while an input field, a form or a one-time credential is what this round
#: forbids.
BOUNDARY_SOURCE_PATTERNS = (
    r"\bticket\b",
    r"type=[\"']?password",
    r"<input[^>]*password",
    r"password[_-]?(field|input|form)",
    r"统一身份",
    r"校园账号",
)


def _validator(definition: str) -> Draft202012Validator:
    schema = contracts.domain_schema()
    sub = dict(schema["$defs"][definition])
    sub["$defs"] = schema["$defs"]
    return Draft202012Validator(sub)


def _paper(**kwargs):
    base = {"paper_id": "p1", "title": "A paper"}
    base.update(kwargs)
    return base


# --------------------------------------------------------------------------
# no authentication machinery anywhere in the package
# --------------------------------------------------------------------------


def _source(name: str, *, strict: bool = False) -> str:
    """Read one module of the package.

    The package-wide scan decodes with ``errors="replace"`` so a peer's bad
    byte cannot crash this control; the four modules this work item owns are
    read strictly, because a v3.2 module must be valid UTF-8.
    """
    raw = (V32_DIR / name).read_bytes()
    return raw.decode("utf-8") if strict else raw.decode("utf-8", "replace")


def test_no_module_in_the_v32_package_contains_authentication_artefacts():
    for path in sorted(V32_DIR.glob("*.py")):
        text = _source(path.name)
        for pattern in CAS_SOURCE_PATTERNS:
            assert not re.search(pattern, text, re.I), f"{path.name} matches {pattern!r}"

    for name in OWNED_MODULES:
        text = _source(name, strict=True)
        for pattern in BOUNDARY_SOURCE_PATTERNS:
            assert not re.search(pattern, text, re.I), f"{name} matches {pattern!r}"


def test_the_access_gateway_opens_no_socket_and_knows_one_url_literal():
    source = _source("access_gateway.py", strict=True)
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for banned in ("urllib", "urllib.request", "http.client", "socket", "requests", "httpx", "aiohttp"):
        assert banned not in imported, banned
    assert all(
        not module.startswith(("urllib", "http", "socket", "requests", "httpx")) for module in imported
    )

    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("http")
    }
    # The module knows exactly two URL-shaped literals: the transport scheme,
    # and the official DOI resolver.  Every institution link comes from
    # contracts/access_links.json, so none may appear here.
    assert literals == {AG.HTTPS_SCHEME, AG.DOI_RESOLVER_PREFIX}
    host_bearing = {value for value in literals if re.match(r"^https?://[^\s/]+", value)}
    assert host_bearing == {AG.DOI_RESOLVER_PREFIX}


def test_module_exposes_nothing_named_like_an_authentication_flow():
    names = [name for name in dir(AG) if re.search(r"cas|login|ticket|password|sso|authenticate", name, re.I)]
    assert names == []


# --------------------------------------------------------------------------
# registry-only institution entries
# --------------------------------------------------------------------------


def test_every_institution_entry_comes_from_the_registry():
    registry = contracts.access_link_registry()
    options = AG.access_options(
        _paper(doi="10.1016/j.example.2024.01.001", publisher="Elsevier")
    )
    institution = [option for option in options if option["kind"] == "institution_link"]

    assert {option["provider"] for option in institution} == set(registry)
    for option in institution:
        assert option["url"] == registry[option["provider"]]["url"]
        assert option["availability"] == "entry_only"
        assert option["granularity"] == registry[option["provider"]]["granularity"]
        _validator("AccessOption").validate(option)


def test_a_custom_registry_is_used_verbatim_and_never_extended():
    custom = {
        "only_entry": {
            "id": "only_entry",
            "url": "https://example.invalid/portal",
            "kind": "institution_link",
            "granularity": "portal",
            "auth_required": True,
            "availability": "entry_only",
            "verified": True,
            "source_url": "https://example.invalid/source",
        }
    }
    options = AG.access_options(_paper(), registry=custom)
    assert [option["url"] for option in options] == ["https://example.invalid/portal"]
    assert options[0]["provider"] == "only_entry"


# --------------------------------------------------------------------------
# article-level links
# --------------------------------------------------------------------------


def test_recorded_landing_page_becomes_a_fulltext_link():
    option = AG.landing_url(_paper(landing_url="https://example.org/article/1", publisher="Example Press"))
    assert option is not None
    assert option["kind"] == "fulltext_link"
    assert option["granularity"] == "article"
    assert option["url"] == "https://example.org/article/1"
    assert option["provider"] == "Example Press"
    assert option["availability"] == "unknown"
    assert option["auth_required"] is True
    _validator("AccessOption").validate(option)


def test_a_doi_only_paper_falls_back_to_the_official_resolver():
    option = AG.landing_url(_paper(doi="https://doi.org/10.1234/AbC"))
    assert option is not None
    assert option["kind"] == "resolver"
    assert option["url"] == "https://doi.org/10.1234/abc"
    assert option["provider"] == "doi"
    assert option["auth_required"] is False
    _validator("AccessOption").validate(option)


def test_open_access_evidence_is_reported_as_public():
    option = AG.landing_url(_paper(landing_url="https://example.org/oa", is_oa=True))
    assert option is not None
    assert option["availability"] == "public"
    assert option["auth_required"] is False


def test_a_paper_without_a_landing_page_or_doi_gets_no_article_link():
    assert AG.landing_url(_paper()) is None
    options = AG.access_options(_paper())
    assert [option["kind"] for option in options] == ["institution_link", "institution_link"]


# --------------------------------------------------------------------------
# publisher-specific database guides
# --------------------------------------------------------------------------


def test_unknown_publisher_gets_a_database_entry_only_and_no_vendor_deep_link():
    options = AG.access_options(_paper(doi="10.1234/unknown-publisher", publisher=None))
    urls = [option["url"] for option in options]
    blob = json.dumps(options, ensure_ascii=False).lower()

    assert "elsevier" not in blob and "sciencedirect" not in blob
    assert any(option["provider"] == "zju_library" for option in options)
    library_entry = next(option for option in options if option["provider"] == "zju_library")
    assert library_entry["availability"] == "entry_only"
    assert library_entry["url"] == contracts.access_link_registry()["zju_library"]["url"]
    assert AG.DOI_RESOLVER_PREFIX in " ".join(urls)  # the DOI is still offered


def test_an_elsevier_paper_also_gets_the_sciencedirect_guide():
    options = AG.access_options(_paper(doi="10.1016/j.example.2024.01.001", publisher="Elsevier"))
    providers = [option["provider"] for option in options]
    assert "zju_sciencedirect" in providers
    guide = next(option for option in options if option["provider"] == "zju_sciencedirect")
    assert guide["url"] == contracts.access_link_registry()["zju_sciencedirect"]["url"]
    assert guide["availability"] == "entry_only"
    assert guide["granularity"] == "database_guide"


# --------------------------------------------------------------------------
# honest labels
# --------------------------------------------------------------------------


def test_auth_required_and_verified_are_reported_without_claiming_entitlement():
    options = AG.access_options(_paper(doi="10.1000/x"))
    by_provider = {option["provider"]: option for option in options}
    assert by_provider["carsi"]["auth_required"] is True
    assert by_provider["zju_library"]["auth_required"] is False
    for option in options:
        assert isinstance(option["verified"], bool)
        assert option["availability"] in {
            "unknown",
            "public",
            "requires_institution",
            "entry_only",
            "unavailable",
        }
        assert option["institution"] in {None, "浙江大学"}
    blob = json.dumps(options, ensure_ascii=False)
    assert "认证成功" not in blob
    assert "全文已授权" not in blob
    assert "已授权" not in blob


def test_checked_at_is_an_rfc3339_timestamp_derived_from_the_registry_date():
    registry_date = contracts.access_links()["checked_at"]
    options = AG.access_options(_paper())
    for option in options:
        assert option["checked_at"].startswith(f"{registry_date}T")
        assert option["checked_at"].endswith("Z")
        assert option["source_url"].startswith("https://")
        assert option["kind"] == "institution_link"
        _validator("AccessOption").validate(option)


# --------------------------------------------------------------------------
# capabilities
# --------------------------------------------------------------------------


def test_capabilities_follow_the_contract_flags():
    report = AG.capabilities_for_access()
    access = contracts.access_defaults()

    assert report["deployment_mode"] == contracts.runtime_defaults()["default_mode"]
    assert set(report) == {"deployment_mode", "items"}
    by_id = {item["id"]: item for item in report["items"]}
    for item in report["items"]:
        _validator("Capability").validate(item)

    assert by_id["external_resource_links"]["status"] == "available"
    assert access["external_resource_links_enabled"] is True
    assert access["application_sso_enabled"] is False
    assert access["federation_sp_enabled"] is False
    # A deferred capability is never reported as available.
    assert by_id["application_sso"]["status"] == "external_blocked"
    assert by_id["federation_sp"]["status"] == "external_blocked"
    assert by_id["cn_metadata_api"]["status"] == "external_blocked"
    assert by_id["application_sso"]["reason_code"] == "deferred_this_round"


def test_access_options_do_not_mutate_their_input():
    registry = contracts.access_link_registry()
    snapshot = json.dumps(registry, sort_keys=True)
    paper = _paper(doi="10.1000/x", publisher="Elsevier")
    AG.access_options(paper, registry=registry)
    assert json.dumps(registry, sort_keys=True) == snapshot
    assert paper == {
        "paper_id": "p1",
        "title": "A paper",
        "doi": "10.1000/x",
        "publisher": "Elsevier",
    }
