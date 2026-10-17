#!/usr/bin/env python3
"""Validate this documentation pack. Does not execute the LEExtractor application.

The pack was written as a standalone directory tree (``contracts/``, ``docs/``,
``fixtures/``, ``handoff/``, ``scripts/`` at its root).  This repository stores
those same files under mapped paths — ``contracts/`` lives inside the shipped
package at ``litsearch/contracts/`` so a wheel and a frozen build find it, and
the specification lives under ``docs/v3.2/``.  ``docs/v3.2/06_REUSE_CHANGE_MAP.md``
explicitly allows a path mapping, so the checks below are unchanged; only the
lookup is routed through :data:`LAYOUT`.

Run from the repository root or from an unpacked pack root; both work, and the
reported ``layout`` says which one was detected.
"""
from pathlib import Path
from collections import Counter
import json, hashlib, math, re, sys, datetime
from urllib.parse import urlparse
R = Path(__file__).resolve().parents[1]

#: pack-relative directory -> candidate locations, first existing one wins.
#: From a real pack root the first candidate is the pack itself, so behaviour
#: there is byte-for-byte what it always was.
LAYOUT = {
    'contracts': (R / 'contracts', R / 'litsearch' / 'contracts', R / 'docs/v3.2' / 'contracts'),
    'fixtures': (R / 'fixtures', R / 'docs/v3.2' / 'fixtures'),
    'docs': (R / 'docs/v3.2', R / 'docs'),
    'handoff': (R / 'handoff',),
    'scripts': (R / 'scripts',),
    'root': (R, R / 'docs/v3.2'),
}


def pack_dir(name: str) -> Path:
    for candidate in LAYOUT[name]:
        if candidate.is_dir():
            return candidate
    raise SystemExit(f'cannot locate the {name!r} directory; tried: '
                     + ', '.join(str(c) for c in LAYOUT[name]))


#: Files that exist at BOTH the pack root and the repository root, with different
#: content: the pack ships the specification's own changelog while the
#: repository ships the product changelog. These must resolve to the pack copy.
PACK_ROOT_FILES = frozenset({'START_HERE.md', 'CHANGELOG.md', 'manifest.json'})

#: Pack file -> the name this repository gives it, when it was renamed on import.
RENAMES = {'scripts/validate_pack.py': 'scripts/validate_v32_pack.py'}


def resolve(rel: str) -> Path:
    """Map a pack-relative path onto its real location in this checkout.

    Resolution order matters:
    1. a documented rename, so the pack's ``scripts/validate_pack.py`` finds this
       repository's ``scripts/validate_v32_pack.py``;
    2. a path that is already valid relative to the repository root, which is how
       implementer-written documents cite each other (``docs/v3.2/qa/...``);
    3. the pack-root files that ALSO exist at the repository root with different
       content, which must stay bound to the pack copy;
    4. the pack directory mapping.
    """
    rel = rel.lstrip('./').rstrip('/')
    if rel in RENAMES:
        return R / RENAMES[rel]
    if rel not in PACK_ROOT_FILES and (R / rel).exists():
        return R / rel
    if rel in PACK_ROOT_FILES and not pack_root_style:
        pack_copy = pack_dir('docs') / rel
        if pack_copy.exists():
            return pack_copy
    if '/' in rel:
        head, tail = rel.split('/', 1)
        if head in LAYOUT and head != 'root':
            candidate = pack_dir(head) / tail
            if candidate.exists():
                return candidate
            renamed = RENAMES.get(rel)
            return R / renamed if renamed else candidate
    for candidate in LAYOUT['root']:
        if (candidate / rel).exists():
            return candidate / rel
    return R / rel


def read(rel, base=None):
    path = (base / rel) if base is not None else resolve(rel)
    return json.loads(path.read_text(encoding='utf-8'))


def check(value, schema, doc, where='$'):
    if '$ref' in schema:
        link = schema['$ref']
        if link.startswith('#/'):
            target = doc
            for part in link[2:].split('/'):
                target = target[part]
            return check(value, target, doc, where)
        raise AssertionError('External instance ref not supported here: ' + link)
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:
                check(value, option, doc, where); return
            except (AssertionError, ValueError, TypeError):
                pass
        raise AssertionError(where + ' no anyOf match')
    if 'const' in schema: assert value == schema['const'], where + ' const'
    if 'enum' in schema: assert value in schema['enum'], where + ' enum'
    types = schema.get('type', []); types = [types] if isinstance(types, str) else types

    def matches(t):
        return {'null': value is None, 'boolean': isinstance(value, bool), 'integer': type(value) == int,
                'number': type(value) in (int, float), 'string': isinstance(value, str),
                'array': isinstance(value, list), 'object': isinstance(value, dict)}[t]
    if types: assert any(matches(t) for t in types), where + ' type'
    if isinstance(value, dict):
        props = schema.get('properties', {})
        assert set(schema.get('required', [])) <= set(value), where + ' missing required'
        if schema.get('additionalProperties') is False: assert set(value) <= set(props), where + ' unknown fields'
        for k, v in value.items():
            if k in props: check(v, props[k], doc, where + '.' + k)
    if isinstance(value, list):
        assert len(value) >= schema.get('minItems', 0), where + ' too few'
        assert len(value) <= schema.get('maxItems', 10 ** 9), where + ' too many'
        if schema.get('uniqueItems'): assert len({json.dumps(v, sort_keys=True) for v in value}) == len(value), where + ' duplicate'
        for i, v in enumerate(value): check(v, schema.get('items', {}), doc, where + f'[{i}]')
    if isinstance(value, str):
        assert len(value) >= schema.get('minLength', 0), where + ' minLength'
        assert len(value) <= schema.get('maxLength', 10 ** 9), where + ' maxLength'
        if schema.get('format') == 'uri': assert urlparse(value).scheme, where + ' uri'
        if schema.get('format') == 'date': datetime.date.fromisoformat(value)
        if schema.get('format') == 'date-time': datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if type(value) in (int, float):
        assert math.isfinite(value), where + ' nonfinite'
        assert value >= schema.get('minimum', -math.inf), where + ' minimum'
        assert value <= schema.get('maximum', math.inf), where + ' maximum'


def walk_refs(node, file):
    if isinstance(node, dict):
        if '$ref' in node:
            path, _, frag = node['$ref'].partition('#')
            target = json.loads((file.parent / path).read_text()) if path else json.loads(file.read_text())
            for part in frag.lstrip('/').split('/'):
                if part: target = target[part.replace('~1', '/').replace('~0', '~')]
        for v in node.values(): walk_refs(v, file)
    elif isinstance(node, list):
        for v in node: walk_refs(v, file)


pack_root_style = (R / 'contracts').is_dir() and (R / 'manifest.json').is_file()
layout = 'pack-root' if pack_root_style else 'repository-mapped'

cfg = read('contracts/defaults.json')
assert cfg['document_version'] == '3.2'
assert abs(sum(cfg['ranking']['weights'].values()) - 1) < 1e-12
assert not cfg['access']['application_sso_enabled']
assert not cfg['access']['federation_sp_enabled']
assert cfg['access']['external_resource_links_enabled']
for f in pack_dir('contracts').glob('*.json'):
    walk_refs(json.loads(f.read_text(encoding='utf-8')), f)
ops = []
for path, methods in read('contracts/openapi.json')['paths'].items():
    assert '/cas/' not in path
    for method, o in methods.items():
        ops.append(o['operationId'])
        names = {p['name'] for p in o.get('parameters', []) if p['in'] == 'path'}
        assert names == set(re.findall(r'\{([^}]+)\}', path))
        assert o['responses']
assert len(ops) == len(set(ops))

rank_count = 0
for c in read('fixtures/ranking_cases.json')['cases']:
    inputs = c['input']; n = len(inputs); ranks = {}
    for sig in cfg['ranking']['weights']:
        known = {k: v.get(sig) for k, v in inputs.items() if v.get(sig) is not None}
        if len(set(round(v, 12) for v in known.values())) < 2: continue
        m = len(known); r = {}
        for key, v in known.items():
            above = sum(round(x, 12) > round(v, 12) for x in known.values())
            tied = sum(round(x, 12) == round(v, 12) for x in known.values())
            known_rank = above + (tied + 1) / 2
            r[key] = 1 + (known_rank - 1) * (n - 1) / (m - 1)
        for key in inputs: r.setdefault(key, (n + 1) / 2)
        ranks[sig] = r
    w = cfg['ranking']['weights']; k = cfg['ranking']['rrf_k']; den = sum(w[s] for s in ranks)
    scores = {p: (sum(w[s] / (k + ranks[s][p]) for s in ranks) / den if den else None) for p in inputs}
    order = sorted(inputs, key=lambda p: (-(round(scores[p], 12) if scores[p] is not None else -1), -inputs[p].get('topic', 0), p))
    assert order == c['expected_order'], (c['id'], order)
    for sig, expected in c.get('expected_signal_ranks', {}).items(): assert ranks[sig] == expected, c['id']
    if c.get('expected_all_scores_null'): assert all(v is None for v in scores.values())
    # Evaluate only arithmetic fixture expressions after a strict character allowlist.
    for p, exp in c.get('expected_raw_expressions', {}).items():
        assert re.fullmatch(r'[0-9.()+*/ -]+', exp)
        expected = eval(exp, {'__builtins__': {}}, {})
        assert math.isclose(scores[p], expected, rel_tol=1e-12), c['id']
    if 'expected_recommended' in c:
        ordered = sorted(inputs, key=lambda p: (-inputs[p].get('topic', 0), p)); cut = math.ceil(.6 * n)
        threshold = inputs[ordered[cut - 1]].get('topic', 0)
        eligible = [p for p in ordered if inputs[p].get('topic', 0) > 0 and inputs[p].get('topic', 0) >= threshold]
        assert eligible == c['expected_recommended'], c['id']
    rank_count += 1

ai = read('fixtures/ai_valid.json'); schema = read('contracts/ai_rerank.schema.json'); check(ai, schema, schema)
sem = read('fixtures/ai_semantic_cases.json')
assert Counter(ai['ordered_paper_ids']) == Counter(sem['input_ids'])
assert Counter(j['paper_id'] for j in ai['judgments']) == Counter(sem['input_ids'])
assert ai['context_hash'] == sem['context_hash']
for bad in sem['invalid_orders']: assert Counter(bad) != Counter(sem['input_ids'])
assert sem['valid_order'] + sem['outer_order'][2:] == sem['expected_merged_order']

states = read('contracts/job_states.json')
assert all(not states['transitions'][s] for s in states['terminal'])
for c in read('fixtures/job_event_cases.json')['cases']:
    outcome = 'commit' if (c['snapshot_context'], c['snapshot_corpus']) == (c['current_context'], c['current_corpus']) else 'superseded'
    assert outcome == c['expected']
cases = read('fixtures/acceptance_cases.json')['cases']; assert len(cases) == 32
assert len({c['id'] for c in cases}) == 32
assert next(c for c in cases if c['id'] == 'AC-29')['status'] == 'deferred'
trace = resolve('docs/18_REQUIREMENTS_TRACEABILITY.md').read_text(encoding='utf-8')
for i in range(1, 23): assert f'R{i:02d}' in trace
for name in ['START_HERE.md', 'docs/20_ZJU_CAS_DEFERRED.md', 'handoff/IMPLEMENTATION_STATUS.md']:
    assert resolve(name).is_file(), name

# Only the pack's own specification documents are checked for balanced fences,
# stale version markers and resolvable relative references. Scanning every
# Markdown file in the repository would flag unrelated product documentation
# that the pack does not ship (for example docs/demo/ and docs/devlog/).
manifest = read('manifest.json')
assert manifest['version'] == '3.2'
spec_documents = [item['path'] for item in manifest['files'] if item['path'].endswith('.md')]

#: Files whose content is EXPECTED to differ from the pack template, each with
#: the reason. Their integrity is reported with both digests rather than
#: asserted, so the difference is visible instead of being silently allowed.
#:
#: ``handoff/IMPLEMENTATION_STATUS.md``
#:     The pack ships an empty template; START_HERE.md requires the implementer
#:     to fill it with the real repository baseline and per-stage evidence.
#: ``scripts/validate_pack.py``
#:     Adapted to this repository's path mapping (``contracts/`` ships inside the
#:     package, the specification lives under ``docs/v3.2/``), which
#:     docs/v3.2/06_REUSE_CHANGE_MAP.md permits. The checks themselves are
#:     unchanged; only the lookup is routed through the layout map.
CONTENT_DIFFERS_FROM_PACK = {
    'handoff/IMPLEMENTATION_STATUS.md': 'implementer-filled status record',
    'scripts/validate_pack.py': 'adapted to the repository path mapping',
    'fixtures/acceptance_cases.json': 'implementer-filled status and implementation_test per case',
}

checksum_verified = 0
checksum_differs = []
line_ending_normalized: list[str] = []


def content_digest(path: Path) -> tuple[str, bool]:
    """SHA256 of the file's *text content*, independent of line endings.

    The manifest records the digests of the pack exactly as authored, with LF
    endings.  A Windows checkout with ``core.autocrlf=true`` rewrites every text
    file to CRLF, which changes the bytes without changing the document; hashing
    the raw bytes then reports a false mismatch for nearly every manifest entry
    (this is exactly how the check first failed after a branch checkout).  This
    normalises CRLF to LF before hashing, so a real content change still fails
    while a checkout convention does not make the pack look corrupt.
    ``.gitattributes`` pins these paths to LF as well; this is the belt to that
    braces.
    """
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(normalized).hexdigest(), normalized != raw


for item in manifest['files']:
    rel = item['path']
    path = resolve(rel)
    assert path.is_file(), f"{rel} -> {path} is missing"
    digest, was_normalized = content_digest(path)
    if was_normalized:
        line_ending_normalized.append(rel)
    if rel in CONTENT_DIFFERS_FROM_PACK:
        checksum_differs.append({'path': rel, 'reason': CONTENT_DIFFERS_FROM_PACK[rel],
                                 'pack_sha256': item['sha256'], 'local_sha256': digest})
        continue
    assert digest == item['sha256'], f'{rel} checksum (mapped to {path})'
    checksum_verified += 1

for rel in spec_documents:
    path = resolve(rel)
    txt = path.read_text(encoding='utf-8')
    assert txt.count('```') % 2 == 0, str(path) + ' unbalanced fences'
    assert not re.search(r'\bv2\.[0-9]', txt), str(path) + ' old version marker'
    for ref in re.findall(r'`((?:docs|contracts|fixtures|scripts|handoff)/[^` ]+)`', txt):
        # A glob is a description of a set of files, not a reference to one, so
        # it cannot be "missing". Prose that names an omitted directory pattern
        # (docs/demo/*.gif) must not read as a broken link.
        if '*' in ref or '?' in ref:
            continue
        assert resolve(ref).exists(), (str(path), ref)

print(json.dumps({
    'status': 'pass',
    'scope': 'documentation contracts and fixed examples only',
    'layout': layout,
    'openapi_operations': len(ops),
    'ranking_fixtures': rank_count,
    'acceptance_specifications': len(cases),
    'requirements': 22,
    'manifest_files_checksum_verified': checksum_verified,
    'manifest_files_content_differs_from_pack': checksum_differs,
    'manifest_files_line_endings_normalized': len(line_ending_normalized),
    'application_tests': 'not_run',
    'schema_validation': 'used-keyword subset; not a full OpenAPI compiler',
}, ensure_ascii=False, indent=2))
