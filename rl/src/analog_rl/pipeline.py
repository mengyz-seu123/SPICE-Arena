from __future__ import annotations

import argparse
import json
import math
import random
import re
import shlex
import subprocess
import sys
from pathlib import Path

from .io import digest, read_jsonl, write_json, write_jsonl


ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / 'agent/trace.py'


def synthesize(output, count, seed, task):
    if count < 1 or task not in {'task1', 'task2', 'both'}:
        raise ValueError('Positive count and task1/task2/both required')
    rng = random.Random(seed)
    experiments = [
        ('gain', 'Inspect gain, headroom and operating point.'),
        ('bandwidth', 'Inspect GBW and signed phase margin at the first valid crossing.'),
        ('slew', 'Inspect both transient edges, slew, settling and rail validity.'),
        ('rejection', 'Inspect CMRR and both PSRR values from the same candidate.'),
        ('power', 'Inspect measured delivered power, area and the bias tradeoff.'),
    ]
    rows = []
    for index in range(count):
        selected = ('task1' if index % 2 == 0 else 'task2') if task == 'both' else task
        focus, hypothesis = experiments[index % len(experiments)]
        hint = round(4 + rng.random() * 30, 1)
        rows.append({'sample_id': f's-{index:06d}', 'task': selected, 'experiment': focus,
                     'prompt': f'Research an OTA candidate for {selected}. {hypothesis} '
                               f'Try initial IBIAS near {hint} uA. Use evaluate_candidate and read_artifact '
                               'to inspect measured evidence and explain the next change. Never combine candidates.',
                     'source': 'synthetic-prompts-v2'})
    path = Path(output).resolve()
    written = write_jsonl(path, rows)
    result = {'stage': 'synthetic', 'count': written, 'seed': seed, 'task': task,
              'dataset': str(path), 'sha256': digest(path), 'note': 'Prompts only; teacher rollout must supply real SFT targets.'}
    write_json(path.with_suffix('.manifest.json'), result)
    return result


def export_trace(path):
    completed = subprocess.run([sys.executable, str(AGENT), 'export', '--run', str(Path(path).resolve())],
                               cwd=ROOT, check=False, text=True, capture_output=True, timeout=120)
    if completed.returncode:
        raise ValueError('Trace export failed: ' + completed.stderr.strip()[:1000])
    return json.loads(completed.stdout)


def checked_trajectory(row, data=None):
    data = export_trace(row['trace']) if data is None else data
    if row['task'] != data['task']:
        raise ValueError('Dataset task differs from sealed trace')
    if not data['eligible']:
        raise ValueError('Trace has no complete, usable assistant trajectory')
    actual = data['prompt_messages'][-1]['content']
    if not (actual.endswith('\nUser instruction:\n' + row['prompt']) or actual == row['prompt']):
        raise ValueError('Dataset prompt differs from the instruction actually sent')
    return data


def finish_preparation(stage, target, data_file, result, command):
    result.update(stage=stage, status='prepared', training_completed=False,
                  trainer_command=command, data_sha256=digest(data_file))
    if command:
        tokens = shlex.split(command)
        if not any('{data}' in token for token in tokens) or not any('{output}' in token for token in tokens):
            raise ValueError('Trainer command must contain {data} and {output} placeholders')
        argv = [token.replace('{data}', str(data_file)).replace('{output}', str(target / 'checkpoint')) for token in tokens]
        completed = subprocess.run(argv, cwd=ROOT, check=False, text=True, capture_output=True)
        (target / 'trainer.stdout.txt').write_text(completed.stdout, encoding='utf-8')
        (target / 'trainer.stderr.txt').write_text(completed.stderr, encoding='utf-8')
        result.update(status='external_command_succeeded' if completed.returncode == 0 else 'failed',
                      returncode=completed.returncode, trainer_argv=argv)
        checkpoint = target / 'checkpoint'
        result['checkpoint'] = str(checkpoint)
        # A non-empty directory can still contain only logs or empty folders.
        # Require an actual model artifact (or a framework checkpoint marker).
        model_files = {'.bin', '.safetensors', '.pt', '.pth', '.ckpt'}
        has_model = checkpoint.is_dir() and any(
            p.is_file() and p.stat().st_size > 0 and
            p.suffix in model_files
            for p in checkpoint.rglob('*'))
        result['checkpoint_valid'] = bool(has_model)
        result['training_completed'] = bool(completed.returncode == 0 and result['checkpoint_valid'])
        if completed.returncode == 0 and not result['checkpoint_valid']:
            result['status'] = 'failed'
            result['error'] = 'Trainer returned 0 but produced no non-empty checkpoint directory'
    result['note'] = ('The repository validates sealed data and checkpoint presence. The external trainer remains '
                      'responsible for optimizer updates, tokenizer/loss masking, and model-specific correctness.')
    write_json(target / 'manifest.json', result)
    return result


def prepare_sft(input_path, output_dir, command=None):
    rows = list(read_jsonl(input_path))
    if not rows:
        raise ValueError('SFT input is empty')
    examples, seen = [], set()
    for row in rows:
        if 'trace' not in row:
            raise ValueError('SFT requires verified teacher traces; synthetic prompts/template answers are not demonstrations')
        data = checked_trajectory(row)
        if not data['valid_metrics']:
            raise ValueError('SFT teacher trace has no valid measured metrics; curate a demonstrated tool trajectory')
        if data['trace_head'] in seen:
            raise ValueError('Duplicate teacher trajectory')
        seen.add(data['trace_head'])
        examples.append({'messages': data['messages'], 'tools': data['tools'],
                         'metadata': {'trace': data['trace'], 'trace_head': data['trace_head'],
                                      'policy': data['policy'], 'verifier': data['verifier'],
                                      'task': data['task'], 'passed': data['passed'],
                                      'valid_metrics': data['valid_metrics']}})
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=False)
    train = target / 'train.jsonl'
    write_jsonl(train, examples)
    return finish_preparation('sft', target, train, {'examples': len(examples), 'train_file': str(train)}, command)


def rollout(input_path, output_dir, config, max_turns, max_evals, rollouts_per_prompt, dry_run):
    rows = list(read_jsonl(input_path))
    if not rows or rollouts_per_prompt < 2 or max_turns < 1 or max_evals < 1:
        raise ValueError('Nonempty prompts, positive budgets and at least two rollouts per prompt required')
    seen = set()
    for row in rows:
        sample = row.get('sample_id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sample) or sample in seen:
            raise ValueError('Unsafe or duplicate sample_id')
        seen.add(sample)
        if row.get('task') not in {'task1', 'task2'} or not isinstance(row.get('prompt'), str) or not row['prompt'].strip():
            raise ValueError('Each sample needs a task and nonempty prompt')
    config = Path(config).resolve()
    if not config.is_file():
        raise ValueError('Provider config does not exist')
    try:
        provider_config = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError('Provider config must be valid JSON: ' + str(error)) from error
    if not isinstance(provider_config, dict):
        raise ValueError('Provider config must be a JSON object')
    config_sha256 = digest(config)
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=False)
    manifest = {'stage': 'rollout', 'status': 'planned' if dry_run else 'running', 'backend': 'direct-api',
                'prompts': len(rows), 'rollouts_per_prompt': rollouts_per_prompt,
                'count': 0, 'dry_run': dry_run, 'config': str(config),
                'config_sha256': config_sha256, 'provider_config': provider_config, 'records': []}
    write_json(target / 'manifest.json', manifest)
    for row in rows:
        for index in range(rollouts_per_prompt):
            rollout_id = f'{row["sample_id"]}-r{index:03d}'
            run_dir = target / rollout_id
            argv = [sys.executable, str(AGENT), 'run', '--task', row['task'], '--config', str(config),
                    '--max-turns', str(max_turns), '--max-evals', str(max_evals),
                    '--out', str(run_dir), '--prompt', row['prompt']]
            record = {k: row[k] for k in ['sample_id', 'task', 'prompt']}
            record.update(rollout_id=rollout_id, trace=str(run_dir), command=argv, status='planned',
                          config_sha256=config_sha256, provider_config=provider_config,
                          policy_version=provider_config.get('policy_version'))
            if not dry_run:
                completed = subprocess.run(argv, cwd=ROOT, check=False, text=True, capture_output=True)
                record.update(status='completed' if completed.returncode in {0, 3} else 'failed', returncode=completed.returncode)
                if completed.returncode not in {0, 3}:
                    record['error'] = completed.stderr[-2000:] or 'See sealed run error and API records'
            manifest['records'].append(record)
            manifest['count'] += 1
            write_json(target / 'manifest.json', manifest)
    if not dry_run:
        manifest['status'] = 'failed' if any(r['status'] == 'failed' for r in manifest['records']) else 'completed'
    write_json(target / 'manifest.json', manifest)
    return manifest


def reward(rollout_manifest, output):
    source = json.loads(Path(rollout_manifest).read_text(encoding='utf-8'))
    records = source.get('records', [])
    if not records:
        raise ValueError('Rollout manifest is empty')
    scored, seen = [], set()
    for record in records:
        if record['rollout_id'] in seen:
            raise ValueError('Duplicate rollout_id')
        seen.add(record['rollout_id'])
        row = {k: record[k] for k in ['sample_id', 'rollout_id', 'prompt', 'task', 'trace']}
        for key in ('config_sha256', 'provider_config', 'policy_version'):
            if key in record:
                row[key] = record[key]
        row.update(trace_verified=False, eligible=False, reward=0.0, reward_normalized=0.0, normalized_metrics={}, passed=False, valid_metrics=0, passed_metrics=0)
        try:
            if record.get('status') == 'planned':
                raise ValueError('Planned rollout has no generated trajectory')
            data = export_trace(record['trace'])
            row.update({k: data[k] for k in ['trace', 'trace_head', 'trace_verified']})
            checked_trajectory(record, data)
            row.update({k: data[k] for k in ['trace', 'trace_head', 'trace_verified', 'eligible', 'reward', 'reward_normalized', 'normalized_metrics',
                                            'passed', 'valid_metrics', 'passed_metrics']})
            row.update(model=data.get('model'), policy_version=data.get('policy_version'),
                       training_reward=data.get('reward_normalized', 0.0))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
            row['exclusion_reason'] = str(error)
        scored.append(row)
    path = Path(output).resolve()
    count = write_jsonl(path, scored)
    eligible = sum(row['eligible'] for row in scored)
    result = {'stage': 'reward', 'status': 'prepared' if eligible else 'no_eligible_samples',
              'count': count, 'eligible': eligible, 'excluded': count - eligible,
              'reward_file': str(path), 'reward_sha256': digest(path),
              'formula': 'legacy pass + valid_metrics/11 + passed_metrics/11; reward_normalized is bounded metric utility'}
    write_json(path.with_suffix('.manifest.json'), result)
    return result


def prepare_grpo(reward_file, output_dir, command=None):
    rows = list(read_jsonl(reward_file))
    if not rows:
        raise ValueError('GRPO input is empty')
    groups, ids, heads = {}, set(), set()
    for row in rows:
        if row['rollout_id'] in ids:
            raise ValueError('Duplicate rollout_id')
        ids.add(row['rollout_id'])
        if row.get('trace_verified') is not True or row.get('eligible') is not True:
            raise ValueError('GRPO input includes excluded/unverified samples; select eligible traces first')
        data = checked_trajectory(row)
        value = row['reward']
        if type(value) not in (float, int) or not math.isfinite(value) or abs(value - data['reward']) > 1e-7:
            raise ValueError('Reward differs from verified measurement evidence')
        training_reward = row.get('training_reward', data.get('reward_normalized', 0.0))
        if (type(training_reward) not in (float, int) or not math.isfinite(training_reward)
                or abs(training_reward - data.get('reward_normalized', 0.0)) > 1e-7):
            raise ValueError('Training reward differs from verified measurement evidence')
        if row.get('trace_head') != data['trace_head'] or data['trace_head'] in heads:
            raise ValueError('Changed or duplicate trajectory')
        heads.add(data['trace_head'])
        groups.setdefault(row['sample_id'], []).append({**row, 'completion': data['completion'],
                                                       'prompt_messages': data['prompt_messages'],
                                                       'policy': data['policy'], 'tools': data['tools'],
                                                       'verifier': data['verifier'],
                                                       'model': data.get('model'),
                                                       'policy_version': data.get('policy_version'),
                                                       'training_reward': training_reward})
    prepared = []
    for sample_id, samples in groups.items():
        first = samples[0]
        if len(samples) < 2:
            raise ValueError('GRPO needs at least two independent rollouts per prompt')
        for sample in samples[1:]:
            if any(sample.get(key) != first.get(key) for key in ['prompt', 'task', 'prompt_messages', 'policy', 'tools', 'verifier',
                                                                 'model', 'policy_version', 'provider_config', 'config_sha256']):
                raise ValueError('GRPO group mixes prompts, tasks, tools, verifiers or behavior policies')
        mean = sum(s['training_reward'] for s in samples) / len(samples)
        std = math.sqrt(sum((s['training_reward'] - mean) ** 2 for s in samples) / len(samples))
        if std <= 1e-12:
            raise ValueError('All group rewards are equal; no group-relative learning signal')
        group = {'sample_id': sample_id, **{key: first.get(key) for key in ['prompt', 'task', 'prompt_messages', 'policy', 'tools', 'verifier',
                                                                            'model', 'policy_version', 'provider_config', 'config_sha256']},
                 'samples': samples, 'reward_mean': mean, 'reward_std': std}
        for sample in samples:
            sample['advantage'] = (sample['training_reward'] - mean) / std
            for key in ['prompt_messages', 'policy', 'tools', 'verifier', 'provider_config']:
                sample.pop(key, None)
        prepared.append(group)
    target = Path(output_dir).resolve()
    target.mkdir(parents=True, exist_ok=False)
    grouped = target / 'groups.jsonl'
    write_jsonl(grouped, prepared)
    return finish_preparation('grpo', target, grouped,
                              {'groups': len(groups), 'samples': len(rows), 'group_file': str(grouped)}, command)


def parser():
    p = argparse.ArgumentParser(description='Verified teacher trajectories and RL data preparation; external training required')
    sub = p.add_subparsers(dest='stage', required=True)
    s = sub.add_parser('synthesize')
    s.add_argument('--out', required=True)
    s.add_argument('--count', type=int, default=100)
    s.add_argument('--seed', type=int, default=7)
    s.add_argument('--task', choices=['task1', 'task2', 'both'], default='both')
    for stage in ['sft', 'grpo']:
        s = sub.add_parser(stage)
        s.add_argument('--input', required=True)
        s.add_argument('--out-dir', required=True)
        s.add_argument('--command', help='External trainer argv with {data} and {output} placeholders')
    s = sub.add_parser('rollout')
    s.add_argument('--input', required=True)
    s.add_argument('--out-dir', required=True)
    s.add_argument('--config', required=True)
    s.add_argument('--max-turns', type=int, default=4)
    s.add_argument('--max-evals', type=int, default=2)
    s.add_argument('--rollouts-per-prompt', type=int, default=4)
    s.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('reward')
    s.add_argument('--rollout-manifest', required=True)
    s.add_argument('--out', required=True)
    s = sub.add_parser('orchestrate', help='Run one complete trace/reward/train/reload round')
    s.add_argument('--input', required=True)
    s.add_argument('--config', required=True)
    s.add_argument('--out-dir', required=True)
    s.add_argument('--mode', choices=['sft', 'grpo'], default='sft')
    s.add_argument('--max-turns', type=int, default=4)
    s.add_argument('--max-evals', type=int, default=2)
    s.add_argument('--rollouts-per-prompt', type=int, default=2)
    s.add_argument('--command', help='Trainer argv with {data} and {output} placeholders')
    s.add_argument('--deploy-command', help='Inference argv with {checkpoint} placeholder')
    s.add_argument('--dry-run', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.stage == 'synthesize':
            result = synthesize(args.out, args.count, args.seed, args.task)
        elif args.stage == 'sft':
            result = prepare_sft(args.input, args.out_dir, args.command)
        elif args.stage == 'rollout':
            result = rollout(args.input, args.out_dir, args.config, args.max_turns, args.max_evals, args.rollouts_per_prompt, args.dry_run)
        elif args.stage == 'reward':
            result = reward(args.rollout_manifest, args.out)
        elif args.stage == 'orchestrate':
            from .orchestrator import TrainingOrchestrator
            result = TrainingOrchestrator(args.out_dir).run(
                args.input, args.config, mode=args.mode,
                max_turns=args.max_turns, max_evals=args.max_evals,
                rollouts_per_prompt=args.rollouts_per_prompt, trainer_command=args.command,
                dry_run=args.dry_run,
                deploy_command=shlex.split(args.deploy_command) if args.deploy_command else None)
        else:
            result = prepare_grpo(args.input, args.out_dir, args.command)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 2 if result.get('status') in {'failed', 'no_eligible_samples'} else 0


if __name__ == '__main__':
    raise SystemExit(main())
