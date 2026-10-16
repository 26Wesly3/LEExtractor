#!/usr/bin/env python3
"""Validate this documentation pack. Does not execute the LEExtractor application."""
from pathlib import Path
from collections import Counter
import json, hashlib, math, re, sys, datetime
from urllib.parse import urlparse
R=Path(__file__).resolve().parents[1]
def read(p):return json.loads((R/p).read_text(encoding='utf-8'))
def check(value,schema,doc,where='$'):
    if '$ref' in schema:
        link=schema['$ref']
        if link.startswith('#/'):
            target=doc
            for part in link[2:].split('/'):target=target[part]
            return check(value,target,doc,where)
        raise AssertionError('External instance ref not supported here: '+link)
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try:check(value,option,doc,where);return
            except (AssertionError,ValueError,TypeError):pass
        raise AssertionError(where+' no anyOf match')
    if 'const' in schema:assert value==schema['const'],where+' const'
    if 'enum' in schema:assert value in schema['enum'],where+' enum'
    types=schema.get('type',[]);types=[types] if isinstance(types,str) else types
    def matches(t):
        return {'null':value is None,'boolean':isinstance(value,bool),'integer':type(value)==int,'number':type(value) in (int,float),'string':isinstance(value,str),'array':isinstance(value,list),'object':isinstance(value,dict)}[t]
    if types:assert any(matches(t) for t in types),where+' type'
    if isinstance(value,dict):
        props=schema.get('properties',{})
        assert set(schema.get('required',[]))<=set(value),where+' missing required'
        if schema.get('additionalProperties') is False:assert set(value)<=set(props),where+' unknown fields'
        for k,v in value.items():
            if k in props:check(v,props[k],doc,where+'.'+k)
    if isinstance(value,list):
        assert len(value)>=schema.get('minItems',0),where+' too few'
        assert len(value)<=schema.get('maxItems',10**9),where+' too many'
        if schema.get('uniqueItems'):assert len({json.dumps(v,sort_keys=True) for v in value})==len(value),where+' duplicate'
        for i,v in enumerate(value):check(v,schema.get('items',{}),doc,where+f'[{i}]')
    if isinstance(value,str):
        assert len(value)>=schema.get('minLength',0),where+' minLength'
        assert len(value)<=schema.get('maxLength',10**9),where+' maxLength'
        if schema.get('format')=='uri':assert urlparse(value).scheme,where+' uri'
        if schema.get('format')=='date':datetime.date.fromisoformat(value)
        if schema.get('format')=='date-time':datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    if type(value) in (int,float):
        assert math.isfinite(value),where+' nonfinite'
        assert value>=schema.get('minimum',-math.inf),where+' minimum'
        assert value<=schema.get('maximum',math.inf),where+' maximum'

def walk_refs(node,file):
    if isinstance(node,dict):
        if '$ref' in node:
            path,_,frag=node['$ref'].partition('#')
            target=json.loads((file.parent/path).read_text()) if path else json.loads(file.read_text())
            for part in frag.lstrip('/').split('/'):
                if part:target=target[part.replace('~1','/').replace('~0','~')]
        for v in node.values():walk_refs(v,file)
    elif isinstance(node,list):
        for v in node:walk_refs(v,file)

cfg=read('contracts/defaults.json')
assert cfg['document_version']=='3.2'
assert abs(sum(cfg['ranking']['weights'].values())-1)<1e-12
assert not cfg['access']['application_sso_enabled']
assert not cfg['access']['federation_sp_enabled']
assert cfg['access']['external_resource_links_enabled']
for f in (R/'contracts').glob('*.json'):walk_refs(json.loads(f.read_text()),f)
ops=[]
for path,methods in read('contracts/openapi.json')['paths'].items():
    assert '/cas/' not in path
    for method,o in methods.items():
        ops.append(o['operationId'])
        names={p['name'] for p in o.get('parameters',[]) if p['in']=='path'}
        assert names==set(re.findall(r'\{([^}]+)\}',path))
        assert o['responses']
assert len(ops)==len(set(ops))

rank_count=0
for c in read('fixtures/ranking_cases.json')['cases']:
    inputs=c['input'];n=len(inputs);ranks={}
    for sig in cfg['ranking']['weights']:
        known={k:v.get(sig) for k,v in inputs.items() if v.get(sig) is not None}
        if len(set(round(v,12) for v in known.values()))<2:continue
        m=len(known);r={}
        for key,v in known.items():
            above=sum(round(x,12)>round(v,12) for x in known.values())
            tied=sum(round(x,12)==round(v,12) for x in known.values())
            known_rank=above+(tied+1)/2
            r[key]=1+(known_rank-1)*(n-1)/(m-1)
        for key in inputs:r.setdefault(key,(n+1)/2)
        ranks[sig]=r
    w=cfg['ranking']['weights'];k=cfg['ranking']['rrf_k'];den=sum(w[s] for s in ranks)
    scores={p:(sum(w[s]/(k+ranks[s][p]) for s in ranks)/den if den else None) for p in inputs}
    order=sorted(inputs,key=lambda p:(-(round(scores[p],12) if scores[p] is not None else -1),-inputs[p].get('topic',0),p))
    assert order==c['expected_order'],(c['id'],order)
    for sig,expected in c.get('expected_signal_ranks',{}).items():assert ranks[sig]==expected,c['id']
    if c.get('expected_all_scores_null'):assert all(v is None for v in scores.values())
    # Evaluate only arithmetic fixture expressions after a strict character allowlist.
    for p,exp in c.get('expected_raw_expressions',{}).items():
        assert re.fullmatch(r'[0-9.()+*/ -]+',exp)
        expected=eval(exp,{'__builtins__':{}},{})
        assert math.isclose(scores[p],expected,rel_tol=1e-12),c['id']
    if 'expected_recommended' in c:
        ordered=sorted(inputs,key=lambda p:(-inputs[p].get('topic',0),p));cut=math.ceil(.6*n)
        threshold=inputs[ordered[cut-1]].get('topic',0)
        eligible=[p for p in ordered if inputs[p].get('topic',0)>0 and inputs[p].get('topic',0)>=threshold]
        assert eligible==c['expected_recommended'],c['id']
    rank_count+=1

ai=read('fixtures/ai_valid.json');schema=read('contracts/ai_rerank.schema.json');check(ai,schema,schema)
sem=read('fixtures/ai_semantic_cases.json')
assert Counter(ai['ordered_paper_ids'])==Counter(sem['input_ids'])
assert Counter(j['paper_id'] for j in ai['judgments'])==Counter(sem['input_ids'])
assert ai['context_hash']==sem['context_hash']
for bad in sem['invalid_orders']:assert Counter(bad)!=Counter(sem['input_ids'])
assert sem['valid_order']+sem['outer_order'][2:]==sem['expected_merged_order']

states=read('contracts/job_states.json')
assert all(not states['transitions'][s] for s in states['terminal'])
for c in read('fixtures/job_event_cases.json')['cases']:
    outcome='commit' if (c['snapshot_context'],c['snapshot_corpus'])==(c['current_context'],c['current_corpus']) else 'superseded'
    assert outcome==c['expected']
cases=read('fixtures/acceptance_cases.json')['cases'];assert len(cases)==32
assert len({c['id'] for c in cases})==32
assert next(c for c in cases if c['id']=='AC-29')['status']=='deferred'
trace=(R/'docs/18_REQUIREMENTS_TRACEABILITY.md').read_text()
for i in range(1,23):assert f'R{i:02d}' in trace
for name in ['START_HERE.md','docs/20_ZJU_CAS_DEFERRED.md','handoff/IMPLEMENTATION_STATUS.md']:assert (R/name).is_file()
for f in R.rglob('*.md'):
    txt=f.read_text()
    assert txt.count('```')%2==0,str(f)+' unbalanced fences'
    assert not re.search(r'\bv2\.[0-9]',txt),str(f)+' old version marker'
    for rel in re.findall(r'`((?:docs|contracts|fixtures|scripts|handoff)/[^` ]+)`',txt):
        assert (R/rel.rstrip('/')).exists(),(str(f),rel)
manifest=read('manifest.json');assert manifest['version']=='3.2'
for item in manifest['files']:
    p=R/item['path'];assert p.is_file(),item['path']
    assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],item['path']+' checksum'
print(json.dumps({'status':'pass','scope':'documentation contracts and fixed examples only','openapi_operations':len(ops),'ranking_fixtures':rank_count,'acceptance_specifications':len(cases),'requirements':22,'application_tests':'not_run','schema_validation':'used-keyword subset; not a full OpenAPI compiler'},ensure_ascii=False,indent=2))
