"""Frozen context-selection comparison and isolated reference-release recovery."""

import argparse
import asyncio
import copy
import hashlib
import json
import platform
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from context_selection import select_context
from evals.check_retrieval import fixtures
from evals.pilot import call_requests, save, sha
from evals.scoring import field_checks
from groq_service import MODEL, report_request
from release_snapshot import active_release, activate_release, read_release, store_release
from retrieval import ReferenceIndex, load_references, source_context

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'evals/context_selection'
POLICIES = ('top_two', 'primary_with_dependencies')


def load_study():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Frozen study file changed: ' + name)
    return manifest


def report_cases():
    notes = [
        ('QF-01', 'Gateway GW-51 uses a Raspberry Pi 5 with fan-control defaults unchanged. From a cold start it warmed to 45 degrees Celsius; the fan remained off. No repairs were attempted and no parts were fitted.', ['RP-PI5-FAN']),
        ('QF-02', 'Gateway GW-52 uses a Raspberry Pi 5 with fan-control defaults unchanged. After warming to 55 degrees Celsius, its temperature fell to 47 degrees and the fan was still running. No repairs were attempted and no parts were fitted.', ['RP-PI5-HYST', 'RP-PI5-FAN']),
        ('QF-03', 'Gateway GW-53 uses a Raspberry Pi 4 Model B. The Arm clock decreased when SoC temperature reached 82 degrees Celsius. GPU clock was not measured. No repairs were attempted and no parts were fitted.', ['RP-THERMAL']),
        ('QF-04', 'Gateway GW-54 uses a Raspberry Pi 5. Its default fan remained off during warming to 45 degrees Celsius. Separately, attached USB disks share a 600mA current budget with a 3A power supply. No repairs were attempted and no parts were fitted.', ['RP-PI5-FAN', 'RP-PI5-USB']),
        ('QF-05', 'Gateway GW-55 uses a Raspberry Pi 4. Its fan stayed off during warming from a cold start to 45 degrees Celsius. The fan configuration was not recorded. No repairs were attempted and no parts were fitted.', []),
    ]
    cases = []
    for id, note, expected in notes:
        source_id = id + '-N1'
        field = lambda state, value: {'state': state, 'value': value, 'source_ids': [source_id]}
        cases.append({'id': id, 'split': 'development', 'family': 'raspberry-pi-existing-sources',
                      'sources': [{'id': source_id, 'type': 'field_note', 'origin': 'synthetic', 'text': note}],
                      'expected_ids': expected, 'reference': {'fields': {
                          'equipment_id': field('known', 'GW-' + str(50 + len(cases) + 1)),
                          'priority': field('unknown', None), 'next_service_date': field('unknown', None),
                          'parts_used': field('none', [])}}})
    return cases


def retrieval_result(case, documents, actual, selection):
    expected = set(case['expected_ids'])
    ids = [d['id'] for d in actual]
    lookup = {d['id']: d for d in documents}
    hits = len(set(ids) & expected)
    wrong = [id for id in ids if case['model'] not in lookup[id]['models'] or lookup[id].get('revision') not in (None, case.get('revision'))]
    denied = [id for id in ids if lookup[id]['status'] != 'current' or lookup[id].get('owner_id') not in (None, case.get('actor'))]
    return {'case_id': case['id'], 'expected_ids': sorted(expected), 'selected_ids': ids,
            'missing_ids': sorted(expected-set(ids)), 'extra_ids': sorted(set(ids)-expected),
            'recall': hits/len(expected) if expected else None,
            'precision': hits/len(ids) if ids else 0 if expected else None,
            'correct_abstention': not ids if not expected else None,
            'wrong_applicability': wrong, 'access_or_revocation': denied, 'selection': selection,
            'complete': not (expected-set(ids)) and not wrong and not denied and (bool(expected) or not ids)}


def retrieval_comparison(output, dependencies):
    sets = [
        ('development', [json.loads(line) for line in (ROOT/'evals/retrieval-cases.jsonl').read_text().splitlines()], load_references()+fixtures(), dependencies),
        ('reserved_challenge', json.loads((DATA/'challenge-cases.json').read_text()), json.loads((DATA/'challenge-documents.json').read_text()), json.loads((DATA/'challenge-dependencies.json').read_text())),
    ]
    rows = []
    for split, cases, documents, deps in sets:
        index = ReferenceIndex(output/(split+'.sqlite3'), documents)
        for policy in POLICIES:
            for case in cases:
                started = time.perf_counter()
                result, selection = select_context(index, case['query'], model=case['model'], revision=case.get('revision'), actor=case.get('actor'), policy=policy, dependencies=deps)
                rows.append(dict(retrieval_result(case, documents, result, selection), split=split, policy=policy, elapsed_ms=round((time.perf_counter()-started)*1000, 4)))
    save(output/'retrieval-comparison.json', rows)
    return rows


def build_item(staging, release_id, case, trace_id, variant):
    release = read_release(staging/'releases', release_id)
    index = ReferenceIndex(staging/'references.sqlite3', release['documents'])
    sources, retrieval = index.enrich(copy.deepcopy(case['sources']), 'evaluation', selection_policy=release['selection_policy'], dependencies=release['dependencies'])
    # Same source-record serialization used by ReportWorkflow.run; evaluator fields are separate.
    items = [{'transcription': json.dumps({k:v for k,v in source.items() if k!='text'})+'\n'+source['text']} for source in sources]
    request = report_request(items)
    request['messages'][0]['content'] = release['instructions']
    for key, value in release['request_settings'].items():
        request[key] = value
    return {'trace_id': trace_id, 'case_id': case['id'], 'variant': variant, 'release_id': release_id,
            'original_sources': case['sources'], 'input_sources': sources, 'retrieval': retrieval, 'source_items': items,
            'request': request, 'request_sha256': sha(request), 'source_sha256': sha(sources)}


def prepare(output):
    study = load_study()
    output.mkdir(parents=True, exist_ok=False)
    staging = output/'staging'; staging.mkdir()
    documents = load_references()
    deps = json.loads((DATA/'manufacturer-dependencies.json').read_text())['dependencies']
    instructions = (ROOT/'report-instructions.txt').read_text()
    settings = {'model': MODEL, 'max_completion_tokens': 768, 'reasoning_effort': 'none', 'temperature': 0.3}
    releases = {policy: store_release(staging/'releases', documents=documents, dependencies=deps, instructions=instructions, request_settings=settings, policy=policy) for policy in POLICIES}
    corrupted = copy.deepcopy(documents)
    for doc in corrupted:
        if doc['id'] in {'RP-PI5-FAN', 'RP-PI5-HYST'}:
            doc['models'] = ['raspberry-pi-4', 'raspberry-pi-5']
            doc['version'] += '-injected-applicability-regression'
    releases['corrupted'] = store_release(staging/'releases', documents=corrupted, dependencies=deps, instructions=instructions, request_settings=settings, policy='top_two')
    activate_release(staging, releases['top_two'])
    cases = report_cases()
    save(output/'study-cases.json', cases)
    save(output/'reference-authority.json', documents)
    retrieval_comparison(output, deps)
    prepared = []
    for round in (1,2):
        for i,case in enumerate(cases[:4]):
            order = POLICIES if (i+round)%2 else POLICIES[::-1]
            for policy in order:
                item = build_item(staging,releases[policy],case,f'{case["id"]}-{policy}-r{round}',policy)
                item['round'] = round
                prepared.append(item)
    for phase,release in [('baseline','top_two'),('regression','corrupted'),('recovered','top_two')]:
        item = build_item(staging,releases[release],cases[4],'recovery-'+phase,phase)
        item['phase'] = phase
        prepared.append(item)
    # Leave the staging index and pointer on the baseline after preparation too.
    build_item(staging,releases['top_two'],cases[4],'unused','baseline')
    manifest = {'name':'frontline-context-recovery-v1','started_at':datetime.now(timezone.utc).isoformat(),
                'status':'prepared','model':MODEL,'provider':'groq','planned_requests':len(prepared),'completed':[], 'attempted':0,
                'interval_seconds':65,'label_status':'Assistant-authored synthetic expectations; no human calibration or field accuracy claim',
                'scope':'Retrieval and report generation from supplied text; isolated staging configuration recovery; no public deployment, UI, audio or image capture',
                'method':'Two policies, four development scenarios, two alternated rounds; three ordered staged release phases. Separate frozen synthetic retrieval challenge.',
                'releases':releases,'study_manifest':study,'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'artifacts':{}}
    for name in ['retrieval.py','context_selection.py','release_snapshot.py','reporting.py','groq_service.py','report-instructions.txt','evals/context_recovery.py','evals/pilot.py','evals/scoring.py']:
        value=(ROOT/name).read_bytes();manifest['artifacts'][name]=sha(value)
        (output/(name.replace('/','__')+'.snapshot')).write_bytes(value)
    save(output/'manifest.json',manifest)
    save(output/'prepared-requests.json',prepared)
    save(output/'review-order.json',[r['trace_id'] for r in prepared])
    return manifest, prepared


def summarize(output):
    manifest=json.loads((output/'manifest.json').read_text())
    cases={c['id']:c for c in json.loads((output/'study-cases.json').read_text())}
    authority=json.loads((output/'reference-authority.json').read_text())
    rows=[]
    for id in manifest['completed']:
        record=json.loads((output/(id+'.json')).read_text());case=cases[record['case_id']]
        context=source_context(case['sources'])
        selected=record['retrieval']['selected_ids']
        docs=[d for d in authority if d['id'] in selected]
        retrieval=retrieval_result(dict(case,model=context['model'],revision=context['revision'],actor='evaluation'),authority,docs,record['retrieval'])
        fields=field_checks(case,record)
        rows.append({'trace_id':id,'case_id':case['id'],'variant':record['variant'],'round':record.get('round'),
                     'http_status':record.get('http_status'),'structure':record['structural_status'],'fields':fields,
                     'retrieval':retrieval,'elapsed_seconds':record.get('elapsed_seconds'),'usage':record.get('usage'),
                     'input_characters':sum(len(s['text']) for s in record['input_sources']),
                     'objective_pass':record['structural_status']=='accepted' and all(c['status']=='pass' for c in fields) and retrieval['complete'],
                     'semantic_review':'pending_assistant_review'})
    save(output/'objective-results.json',rows)
    return rows


async def execute(args):
    output=args.output
    if args.interval<65:raise ValueError('Use at least 65 seconds between requests')
    manifest,prepared=prepare(output)
    print(f'Prepared {len(prepared)} frozen requests in {output}',flush=True)
    if args.prepare:return
    manifest['status']='running';manifest['interval_seconds']=args.interval
    await call_requests(args,output,prepared[:16],manifest)
    if len(manifest['completed'])!=16 or manifest['status']!='finished':
        summarize(output);return
    transitions=[];staging=output/'staging'
    try:
        for item in prepared[16:]:
            # Conservative spacing from the previous completion when starting another batch.
            await asyncio.sleep(args.interval)
            preserved={id:sha((output/(id+'.json')).read_bytes()) for id in manifest['completed']}
            before=active_release(staging)[0]
            started=time.perf_counter()
            activate_release(staging,item['release_id'])
            case=next(c for c in report_cases() if c['id']==item['case_id'])
            rebuilt=build_item(staging,item['release_id'],case,item['trace_id'],item['variant'])
            if rebuilt['request_sha256']!=item['request_sha256']:
                raise ValueError('Activated release differs from its frozen request')
            transition={'phase':item['phase'],'previous_release':before,'active_release':item['release_id'],
                        'activation_and_index_ms':round((time.perf_counter()-started)*1000,3),
                        'request_matches_frozen':True}
            manifest['status']='running_recovery'
            await call_requests(args,output,[dict(item,transition=transition)],manifest)
            transition['prior_reports_unchanged']=all(sha((output/(id+'.json')).read_bytes())==digest for id,digest in preserved.items())
            transitions.append(transition);save(output/'release-transitions.json',transitions)
            if manifest['status']!='finished':break
    finally:
        # Recovery is required even if execution or preflight raises unexpectedly.
        activate_release(staging,manifest['releases']['top_two'])
        build_item(staging,manifest['releases']['top_two'],report_cases()[4],'unused','baseline')
    manifest['status']='finished' if len(manifest['completed'])==len(prepared) else 'incomplete'
    save(output/'manifest.json',manifest)
    rows=summarize(output)
    print(json.dumps({'completed':len(rows),'http_200':sum(r['http_status']==200 for r in rows),
                      'objective_passes':sum(r['objective_pass'] for r in rows),'staging_restored':active_release(staging)[0]==manifest['releases']['top_two']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--credentials',type=Path)
    parser.add_argument('--interval',type=int,default=65)
    parser.add_argument('--prepare',action='store_true')
    asyncio.run(execute(parser.parse_args()))
