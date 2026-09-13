"""Read-only comparison report for the frozen context/recovery study."""

import argparse
import html
import json
import statistics
from pathlib import Path
from urllib.parse import quote

from evals.context_recovery import summarize
from evals.trace_data import output_fingerprint


def median(values):
    return statistics.median(values) if values else None


def number(value):
    return 'unavailable' if value is None else f'{value:.3f}'


def render(run, review_url='http://127.0.0.1:5004'):
    run=Path(run)
    manifest=json.loads((run/'manifest.json').read_text())
    rows=summarize(run)
    selections=json.loads((run/'retrieval-comparison.json').read_text())
    judgments={}
    if (run/'assistant-review.json').exists():
        data=json.loads((run/'assistant-review.json').read_text())
        if data['reviewer_kind']!='assistant':raise ValueError('Expected explicitly assistant-authored review')
        for item in data['reviews']:
            raw=json.loads((run/(item['trace_id']+'.json')).read_text())
            if item['output_sha256']!=output_fingerprint(raw):raise ValueError('Review/output fingerprint mismatch')
            judgments[item['trace_id']]=item
    stats=[]
    for split in ['development','reserved_challenge']:
        for policy in ['top_two','primary_with_dependencies']:
            cases=[r for r in selections if r['split']==split and r['policy']==policy]
            answerable=[r for r in cases if r['expected_ids']]
            stats.append({'split':split,'policy':policy,'complete':sum(r['complete'] for r in cases),'cases':len(cases),
                          'precision':sum(r['precision'] for r in answerable)/len(answerable),
                          'recall':sum(r['recall'] for r in answerable)/len(answerable),
                          'selected_characters':sum(r['selection']['selected_characters'] for r in cases)})
    comparison=[r for r in rows if r['round'] is not None]
    summaries=[];paired=[]
    for policy in ['top_two','primary_with_dependencies']:
        cases=[r for r in comparison if r['variant']==policy]
        summaries.append({'policy':policy,'responses':len(cases),'http_200':sum(r['http_status']==200 for r in cases),
                          'structurally_accepted':sum(r['structure']=='accepted' for r in cases),
                          'reference_complete':sum(r['retrieval']['complete'] for r in cases),
                          'field_passes':sum(all(c['status']=='pass' for c in r['fields']) for r in cases),
                          'median_http_seconds':median([r['elapsed_seconds'] for r in cases if r['elapsed_seconds'] is not None]),
                          'reported_input_tokens':sum((r['usage'] or {}).get('prompt_tokens',0) for r in cases),
                          'reported_output_tokens':sum((r['usage'] or {}).get('completion_tokens',0) for r in cases),
                          'missing_usage':sum(not r['usage'] for r in cases)})
    for id,round in sorted({(r['case_id'],r['round']) for r in comparison}):
        arms={r['variant']:r for r in comparison if r['case_id']==id and r['round']==round}
        if len(arms)==2 and all(r['elapsed_seconds'] is not None for r in arms.values()):
            paired.append({'case':id,'round':round,'candidate_minus_baseline_seconds':arms['primary_with_dependencies']['elapsed_seconds']-arms['top_two']['elapsed_seconds']})
    failures=[r['trace_id'] for r in comparison if r['variant']=='primary_with_dependencies' and not r['retrieval']['complete']]
    challenge_losses=[]
    for r in selections:
        if r['split']=='reserved_challenge' and r['policy']=='primary_with_dependencies' and not r['complete']:
            baseline=next(x for x in selections if x['split']==r['split'] and x['case_id']==r['case_id'] and x['policy']=='top_two')
            if baseline['complete']:challenge_losses.append(r['case_id'])
    recovery=[r for r in rows if r['round'] is None]
    complete=len(rows)==manifest['planned_requests']
    decision={'complete_run':complete,'candidate_disposition':'retain_baseline' if failures or challenge_losses else 'needs_review',
              'candidate_reference_failures':failures,'challenge_regressions':challenge_losses,
              'semantic_reviews':len(judgments),'human_calibration':False,
              'recovery':{r['variant']:{'http_status':r['http_status'],'structure':r['structure'],'applicability_pass':not r['retrieval']['wrong_applicability'],'objective_pass':r['objective_pass']} for r in recovery}}
    summary={'retrieval':stats,'report_comparison':summaries,'paired_timing':paired,
             'median_paired_delta_seconds':median([r['candidate_minus_baseline_seconds'] for r in paired]),'decision':decision}
    (run/'comparison-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    esc=lambda x:html.escape(str(x))
    def table(headers,body):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in body)+'</tbody></table></div>'
    content='<h1>Context selection and recovery</h1><p>A frozen comparison using real Groq responses and a separate synthetic retrieval challenge.</p>'
    content+=f'<p><strong>{len(rows)}/{manifest["planned_requests"]} responses recorded.</strong> Candidate decision: {esc(decision["candidate_disposition"])}. Human calibration is not claimed.</p>'
    content+='<p>The candidate takes the first ranked passage plus declared dependencies. The baseline takes the top two. Scores and omissions below remain separate; a schema-valid report can still lack necessary context.</p>'
    content+='<h2>Retrieval before generation</h2>'+table(['Set','Policy','Complete','Precision','Recall','Selected characters'],[[esc(r['split']),esc(r['policy']),f'{r["complete"]}/{r["cases"]}',number(r['precision']),number(r['recall']),r['selected_characters']] for r in stats])
    content+='<p class="note">The original 20 cases are development data. The ten challenge cases use five fictional source families frozen before this comparison. These are assistant-authored labels, not a field distribution or independent human assessment.</p>'
    content+='<h2>Generated-report comparison</h2>'+table(['Policy','HTTP 200','Valid structure','Fields correct','Required references present','Median HTTP seconds','Input tokens','Output tokens'],[[esc(r['policy']),f'{r["http_200"]}/{r["responses"]}',r['structurally_accepted'],r['field_passes'],r['reference_complete'],number(r['median_http_seconds']),r['reported_input_tokens'],r['reported_output_tokens']] for r in summaries])
    content+='<p class="note">Two rounds over four scenarios; not eight independent situations. HTTP timing excludes selection, UI and rate-limit spacing. Tokens are provider-reported; no dollar cost is inferred. Known fields do not grade free-text faithfulness.</p>'
    content+='<h2>Staged release recovery</h2>'+table(['Phase','HTTP','Structure','Wrong-applicability IDs','Objective checks'],[[esc(r['variant']),esc(r['http_status']),esc(r['structure']),esc(', '.join(r['retrieval']['wrong_applicability']) or 'none'),'pass' if r['objective_pass'] else 'fail'] for r in recovery])
    if (run/'release-transitions.json').exists():
        transitions=json.loads((run/'release-transitions.json').read_text())
        content+='<details><summary>Exact release IDs, activation time, and preserved reports</summary><pre>'+esc(json.dumps(transitions,indent=2))+'</pre></details>'
    content+='<p class="note">The deliberately corrupted metadata is used only in a local staging snapshot. This is a scripted configuration rollback, not production downtime or an autonomous deployment system. Applicability is checked against the original unchanged reference authority.</p>'
    content+='<h2>Trace-by-trace review</h2><p>Open a trace for the original source packet, generated report, request, retrieval metadata and local annotation controls. Assistant critiques are separate from your review journal.</p>'
    for r in rows:
        link=review_url+'/?trace='+quote(r['trace_id'])
        j=judgments.get(r['trace_id'])
        content+='<article id="'+esc(r['trace_id'])+'"><h3><a href="'+esc(link)+'">'+esc(r['trace_id'])+'</a></h3>'
        content+='<p>Required context: '+esc(', '.join(r['retrieval']['expected_ids']) or 'none')+'<br>Selected: '+esc(', '.join(r['retrieval']['selected_ids']) or 'none')+'</p>'
        if r['retrieval']['missing_ids']:content+='<p class="failure">Missing: '+esc(', '.join(r['retrieval']['missing_ids']))+'</p>'
        if j:content+='<p><strong>Assistant faithfulness '+esc(j['verdict'])+':</strong> '+esc(j['critique'])+'</p>'
        else:content+='<p class="note">Prose review not yet recorded.</p>'
        raw=json.loads((run/(r['trace_id']+'.json')).read_text())
        content+='<details><summary>Sources and output without opening the review server</summary><h4>Original notes</h4><pre>'+esc(json.dumps(raw['original_sources'],indent=2))+'</pre><h4>Generated output</h4><pre>'+esc(raw.get('raw_output') or raw.get('response_body','No output'))+'</pre></details></article>'
    page='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Frontline context/recovery review</title><style>body{max-width:1150px;margin:30px auto;padding:0 22px;font:16px/1.6 system-ui;color:#382d24;background:#f7f1e7}h1,h2,h3{line-height:1.2}a{color:#784b2d}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{padding:9px;text-align:left;border:1px solid #cdbca5}th{background:#eaddca}.note{color:#675b4f;font-size:14px}article{padding:18px;border:1px solid #cdbca5;background:#fffcf6;margin:18px 0}.failure{color:#9c3528}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}summary{cursor:pointer}</style>'+content+'</html>'
    (run/'report.html').write_text(page)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path,required=True)
    parser.add_argument('--review-url',default='http://127.0.0.1:5004')
    args=parser.parse_args()
    print(json.dumps(render(args.run,args.review_url),indent=2))
