from __future__ import annotations
import csv
import html
import json
from pathlib import Path
from urllib.parse import quote
from .tasks import task_config
from .submission import success_levels, level_counts, preflight_counts


def render_report(root):
    root = Path(root)
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((root / 'evaluations').glob('*/assessment.json'))]
    selected_task = reports[0]['selected_task'] if reports else 'ota'
    names = list(task_config(selected_task)['constraints'])
    with (root / 'metrics.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['candidate_id', 'status', 'selected_task', 'passed', 'execution_valid', 'functional_valid', 'performance_passed', 'spice_evaluations', *names])
        writer.writeheader()
        for r in reports:
            writer.writerow({'candidate_id': r['candidate_id'], 'status': r['status'], 'selected_task': r['selected_task'],
                             'passed': r['assessment']['passed'],
                             **success_levels(r), 'spice_evaluations':r.get('spice_evaluations',0),
                             **{n: r['metrics'].get(n) for n in names}})
    events = []
    if (root / 'events.jsonl').is_file():
        with (root / 'events.jsonl').open(encoding='utf-8') as f:
            for line in f:
                event = json.loads(line)
                if event['type'] not in {'provider.delta', 'provider.sse_line'}:
                    events.append(event)
    summary = json.loads((root / 'summary.json').read_text(encoding='utf-8')) if (root / 'summary.json').is_file() else {}
    summary = {**summary, 'levels':level_counts(reports), 'preflight':preflight_counts(root)}
    (root / 'quality-summary.json').write_text(json.dumps({'levels':level_counts(reports), 'evaluated_candidates':len(reports),
        'preflight':preflight_counts(root), 'spice_evaluations':sum(r.get('spice_evaluations') or 0 for r in reports)},indent=2),encoding='utf-8')
    esc = lambda obj: html.escape(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False))
    rows = []
    for r in reports:
        checks = r['assessment']['checks']
        cells = ''.join('<td class="' + ('pass' if checks[n]['passed'] else 'fail') + '">' + html.escape(str(checks[n]['value'])) + (' ⚠' if not checks[n]['valid'] else '') + '</td>' for n in names)
        rows.append('<tr><td><a href="evaluations/' + r['candidate_id'] + '/assessment.json">' + r['candidate_id'] + '</a></td><td>' + html.escape(r['selected_task']) + '</td><td>' + str(r['assessment']['passed']) + '</td>' + cells + '</tr>')
    timeline = []
    reasoning_count = 0
    for e in events:
        data = dict(e['data'])
        request_id = data.get('request_id')
        if e['type'] == 'provider.request' and 'body' in data and request_id:
            body = data.pop('body')
            data.update(model=body.get('model'), message_count=len(body.get('messages', [])),
                        request_file=f'api/{request_id}/request.json')
        if e['type'] == 'provider.completed':
            fragments = data.pop('reasoning', [])
            reasoning_count += data.get('reasoning_fragments', len(fragments))
            if request_id:
                data.pop('message', None)
                data['response_file'] = f'api/{request_id}/response.json'
        if e['type'] == 'process.completed' and data.get('candidate_id'):
            for channel in ('stdout', 'stderr'):
                data.pop(channel, None)
                data[channel + '_file'] = f"evaluations/{data['candidate_id']}/{channel}.txt"
        links = []
        for key, value in data.items():
            if key.endswith('_file') and isinstance(value, str):
                path = Path(value)
                if not path.is_absolute() and '..' not in path.parts and not ':' in value and not '\\' in value:
                    links.append('<a href="' + html.escape(quote(value), quote=True) + '">' + html.escape(value) + '</a>')
        timeline.append('<details data-kind="' + html.escape(e['type']) + '"><summary>' + html.escape(f"{e['seq']} · {e['time']} · {e['type']}") + '</summary><pre>' + esc(data) + '</pre>' + ' · '.join(links) + '</details>')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Analog Arena Trace</title><style>
body{font:15px/1.6 system-ui,sans-serif;max-width:1500px;margin:32px auto;padding:0 24px;background:#f5f7fa;color:#172334}
h1{font-size:30px}section{background:white;padding:20px;margin:18px 0;border:1px solid #dbe2ec;border-radius:10px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}details{border-bottom:1px solid #ddd;padding:10px}summary{cursor:pointer}
table{border-collapse:collapse;font-size:12px}th,td{padding:8px;border:1px solid #ddd;text-align:right}th{background:#e8eef7}
.pass{background:#e6f5eb}.fail{background:#fce9e6}.scroll{overflow-x:auto}input{padding:8px;width:300px;max-width:90%}a{color:#155fc1}
</style><h1>Analog Arena · Experiment report</h1>
<p>Only returned reasoning fields and client events are shown. Warning symbols mark invalid or missing metrics. Task1 is stricter than task2.</p>
<section><h2>Run results</h2><pre>SUMMARY</pre><p>Captured provider reasoning segments: REASONCOUNT</p>
<a href="events.jsonl">Full event JSONL</a> · <a href="messages.json">Messages</a> · <a href="metrics.csv">Metrics CSV</a></section>
<section><h2>Candidate metrics</h2><p>Colors reflect the selected task. Success also requires valid execution and one TT candidate. Metrics are not combined across candidates.</p><div class="scroll"><table><thead><tr><th>Candidate</th><th>Task</th><th>Pass</th>HEADS</tr></thead><tbody>ROWS</tbody></table></div></section>
<section><h2>Events and reasoning</h2><p>Events summarize execution; requests, raw SSE, responses and simulation logs are stored separately.</p>
<input id="filter" placeholder="Filter by event type, e.g. provider.completed">TIMELINE</section>
<script>document.getElementById('filter').addEventListener('input',e=>{document.querySelectorAll('details').forEach(d=>d.hidden=!d.dataset.kind.includes(e.target.value))})</script></html>'''
    page = page.replace('SUMMARY', esc(summary)).replace('REASONCOUNT', str(reasoning_count)).replace('HEADS', ''.join('<th>' + n + '</th>' for n in names)).replace('ROWS', ''.join(rows)).replace('TIMELINE', ''.join(timeline))
    (root / 'report.html').write_text(page, encoding='utf-8')
    return root / 'report.html'
