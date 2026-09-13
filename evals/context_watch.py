"""Observe study outputs and optionally restore their isolated staging configuration."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from evals.context_recovery import summarize
from release_snapshot import active_release, activate_release
from retrieval import ReferenceIndex


def watch(run, *, restore_staging=False, timeout=600):
    run=Path(run).resolve();staging=run/'staging';seen=set();start=time.monotonic()
    if staging.is_symlink():raise ValueError('Staging must belong to the study directory')
    journal=run/'monitor-events.jsonl'
    if journal.exists():raise ValueError('Use the existing monitor record; do not overwrite it')
    def emit(event):
        event['at']=datetime.now(timezone.utc).isoformat()
        with journal.open('a') as out:out.write(json.dumps(event)+'\n')
        print(json.dumps(event),flush=True)
    emit({'event':'monitor_started','scope':'isolated study staging only','automatic_staging_restore':restore_staging})
    while time.monotonic()-start<timeout:
        try:
            manifest=json.loads((run/'manifest.json').read_text())
            if manifest['name']!='frontline-context-recovery-v1':raise ValueError('Unsupported study')
            rows=summarize(run)
        except json.JSONDecodeError:
            time.sleep(.2);continue
        for row in rows:
            if row['trace_id'] in seen:continue
            seen.add(row['trace_id'])
            if row['objective_pass']:continue
            raw=json.loads((run/(row['trace_id']+'.json')).read_text())
            emit({'event':'quality_check_failed','trace_id':row['trace_id'],'http_status':row['http_status'],
                  'structure':row['structure'],'missing_references':row['retrieval']['missing_ids'],
                  'wrong_applicability':row['retrieval']['wrong_applicability'],
                  'failed_fields':[c['criterion'] for c in row['fields'] if c['status']!='pass']})
            active=active_release(staging)[0]
            stable=manifest['releases']['top_two']
            if restore_staging and active==raw['release_id'] and active!=stable:
                began=time.perf_counter()
                release=activate_release(staging,stable)
                ReferenceIndex(staging/'references.sqlite3',release['documents'])
                emit({'event':'staging_restored','trigger_trace':row['trace_id'],'previous_release':active,
                      'restored_release':stable,'restore_ms':round((time.perf_counter()-began)*1000,3),
                      'production_deployment_changed':False})
        if len(manifest['completed'])==manifest['planned_requests']:
            emit({'event':'monitor_finished','traces_observed':len(seen)});return
        time.sleep(1)
    emit({'event':'monitor_timeout','traces_observed':len(seen)})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--restore-staging',action='store_true')
    parser.add_argument('--timeout',type=int,default=600)
    args=parser.parse_args();watch(args.run,restore_staging=args.restore_staging,timeout=args.timeout)
