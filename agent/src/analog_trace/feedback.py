"""Model-facing views; full measurements remain in assessment.json."""
from __future__ import annotations

import json
import re

REVISION = 'ota-feedback-20260912-v3'


def grouped_diagnostics(diagnostics):
    groups = {}
    for item in diagnostics:
        key = item.get('code', 'unknown')
        if key not in groups:
            groups[key] = {**item, 'affected_lines': [], 'reported_occurrences': 0}
        group = groups[key]
        group['reported_occurrences'] += 1
        if 'line' in item:
            group['affected_lines'].append(item['line'])
    return list(groups.values())


def enrich_report(report, base):
    """Add bounded observations only; never alter the DUT or acceptance result."""
    if not report.get('selected_task', '').startswith('OTA-'):
        return
    request_path = base / 'request.json'
    if not request_path.is_file():
        return
    request = json.loads(request_path.read_text(encoding='utf-8'))
    raw = request.get('netlist', '')
    from analog_arena.simulation.amplifier.candidate import _located_lines, MOS_MODELS
    try:
        located = _located_lines(raw)
    except ValueError:
        located = []
    lines = dict(located)
    commented = [i for i, line in enumerate(raw.splitlines(), 1)
                 if re.match(r'^\s*\*\s*[XRC]\S*\s+', line, re.I)]
    active = [(i, line) for i, line in located if line and line[0].lower() in 'xrc']
    diagnostics = report.get('diagnostics')
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    facts = {'advisory_only': True}
    if report.get('status') != 'VALID' and diagnostics:
        issues = []
        for d in grouped_diagnostics(diagnostics):
            tokens = lines.get(d.get('line'), '').split()
            issue = {'code': d.get('code'), 'line': d.get('line')}
            if tokens:
                issue.update(actual_field_count=len(tokens), element=tokens[0])
                if tokens[0].lower().startswith('x'):
                    issue.update(instance_name_is_bare_x=tokens[0].lower() == 'x',
                                 hint='The instance name is ONE token starting with X, followed by exactly D G S B model; W/L/M follow. Do not add another name or terminal field.')
                elif d.get('code') in ('resistor_fields', 'capacitor_fields'):
                    issue.update(expected_field_count=4, value_tokens=tokens[3:11],
                                 hint='The value and its single k or p suffix form ONE token, without spaces or a repeated unit. Node connections are your design choice.')
            issues.append(issue)
        facts['syntax'] = {'active_device_lines': len(active),
                           'commented_device_lines': commented[:20],
                           'commented_device_count': len(commented), 'issues': issues}
        if not active and commented:
            facts['syntax']['hint'] = 'All detected device statements start with * and are comments, not active devices. Supply your intended device statements as active SPICE lines.'
        code = diagnostics[0].get('code')
        streak = 1
        previous = sorted((p for p in base.parent.glob('*/assessment.json') if p.parent != base),
                          key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in previous:
            old = json.loads(path.read_text(encoding='utf-8'))
            ds = old.get('diagnostics')
            if old.get('status') == 'VALID' or not isinstance(ds, list) or not ds or ds[0].get('code') != code:
                break
            streak += 1
        if streak > 1:
            facts['repeated_primary_error'] = {'code': code, 'consecutive_candidates': streak,
                                               'hint': 'The same primary syntax issue remains. Fix its reported fields before changing unrelated circuit parameters.'}

    ports = {name: [] for name in ('VDD', 'GND', 'VINP', 'VINN', 'VOUT', 'IBIAS')}
    for _, line in active:
        tokens = line.split()
        if tokens[0].lower().startswith('x') and len(tokens) >= 6 and tokens[5].lower() in MOS_MODELS:
            entries = zip(tokens[1:5], ('drain', 'gate', 'source', 'body'))
        elif tokens[0][0].lower() in 'rc' and len(tokens) == 4:
            entries = zip(tokens[1:3], ('passive_terminal', 'passive_terminal'))
        else:
            continue
        for node, role in entries:
            if node.upper() in ports:
                ports[node.upper()].append(role)
    if report.get('status') != 'VALID' or not report.get('assessment', {}).get('functional_valid'):
        facts['ports'] = {'unused': [name for name, roles in ports.items() if not roles],
                          'ibias_terminal_roles': sorted(set(ports['IBIAS'])),
                          'scope': 'References in recognizable active device lines only; graph connectivity does not prove a conducting DC path or correct bias.'}

    paths = []
    def available(path):
        if path.is_file() and path.resolve().is_relative_to(base.resolve()):
            relative = report['artifact_path'] + '/' + path.relative_to(base).as_posix()
            if relative not in paths:
                paths.append(relative)
            return relative
        return None
    for name in ('dut.spice', 'request.json', 'simulation/result.json', 'simulation/ota/result.json'):
        available(base / name)
    if report.get('status') != 'VALID' and not diagnostics:
        evidence = {'failed_checks': [], 'errors': []}
        result_file = base / 'simulation/ota/result.json'
        if available(result_file):
            try:
                data = json.loads(result_file.read_text(encoding='utf-8'))
                evidence['failed_checks'] = data.get('failed_checks', [])[:16]
            except (ValueError, OSError):
                pass
        seen = set()
        for log in sorted((base / 'simulation/ota').glob('*/*.log'))[:12]:
            relative = available(log)
            if not relative:
                continue
            with log.open('rb') as stream:
                head = stream.read(32768)
                stream.seek(max(len(head), log.stat().st_size - 32768))
                text = (head + stream.read(32768)).decode('utf-8', errors='replace')
            for line in text.splitlines():
                if re.search(r'error:|singular matrix|timestep too small|simulation.*aborted|stepping failed', line, re.I):
                    message = line.strip()[:400]
                    if message not in seen and len(evidence['errors']) < 6:
                        evidence['errors'].append({'path': relative, 'message': message})
                        seen.add(message)
        facts['execution'] = evidence
    facts['artifact_paths'] = paths[:16]
    report['agent_feedback'] = facts


def evaluation_feedback(report, remaining):
    # Preserve unexpected/custom reports instead of silently discarding them.
    if 'assessment' not in report or 'artifact_path' not in report:
        return report
    assessment = report['assessment']
    result = {key: report.get(key) for key in
              ('candidate_id', 'status', 'spice_evaluations', 'artifact_path')}
    result.update(feedback_revision=REVISION, remaining=max(0, remaining))
    from .submission import success_levels
    result['levels'] = report.get('levels', success_levels(report))
    result['measurement_complete'] = report.get('measurement_complete', all(c.get('valid') for c in assessment.get('checks', {}).values()) and bool(assessment.get('checks')))
    result['assessment'] = {'passed': bool(assessment.get('passed')),
                            'functional_valid': bool(assessment.get('functional_valid'))}
    result['report_path'] = report['artifact_path'] + '/assessment.json'
    if report.get('change_summary'):
        result['change_summary'] = report['change_summary']
    if report.get('agent_feedback'):
        result['agent_feedback'] = report['agent_feedback']
    if report.get('status') != 'VALID' and isinstance(report.get('diagnostics'), list) and report['diagnostics']:
        result.update(stage='netlist_validation', diagnostics=grouped_diagnostics(report['diagnostics']),
                      diagnostics_truncated=bool(report.get('diagnostics_truncated')))
    elif report.get('status') != 'VALID':
        result.update(stage='execution_or_measurement', error=(report.get('error') or 'Execution/measurement invalid')[:600],
                      failed_checks=report.get('failed_checks', []))
    else:
        result['stage'] = 'measured'
        # Keep every required metric, its limits, validity and pass flag once.
        result['assessment']['checks'] = assessment.get('checks', {})
        result['functional_checks'] = report.get('functional_checks', {})
    return result


def evaluation_history(reports, remaining):
    entries = []
    for report in reports:
        assessment = report.get('assessment', {})
        checks = assessment.get('checks', {})
        entries.append({'candidate_id': report.get('candidate_id'),
                        'status': report.get('status'), 'passed': bool(assessment.get('passed')),
                        'functional_valid': bool(assessment.get('functional_valid')),
                        'valid_metrics': sum(bool(c.get('valid')) for c in checks.values()),
                        'passed_metrics': sum(bool(c.get('passed')) for c in checks.values()),
                        'report_path': report.get('artifact_path', '') + '/assessment.json'})
        from .submission import success_levels
        entries[-1]['levels'] = report.get('levels', success_levels(report))
    return {'evaluations': entries, 'remaining': max(0, remaining)}
