"""Repeatable trace -> simulation -> reward -> train -> reload orchestration."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from .io import write_json
from .pipeline import rollout, reward, prepare_sft, prepare_grpo


class TrainingOrchestrator:
    def __init__(self, work_dir):
        self.root = Path(work_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run(self, prompts, config, mode='sft', rollouts_per_prompt=2,
            max_turns=4, max_evals=2, trainer_command=None, dry_run=False,
            deploy_command=None):
        """Execute one reproducible round and return its manifest.

        ``prompts`` is a JSONL path. The stage manifests are written under
        ``work_dir`` and contain hashes/commands so a later round can resume
        or audit each boundary.
        """
        prompts = Path(prompts).resolve(); config = Path(config).resolve()
        # Allocate a fresh immutable round directory so repeated calls form a
        # resumable chain instead of colliding with prior artifacts.
        index = 1
        while (self.root / f'round-{index:03d}').exists():
            index += 1
        rounds = self.root / f'round-{index:03d}'
        rounds.mkdir()
        roll_dir = rounds / 'rollouts'
        rmanifest = rollout(prompts, roll_dir, config, max_turns, max_evals,
                            rollouts_per_prompt, dry_run)
        if dry_run:
            manifest = {'mode': mode, 'prompts': str(prompts), 'config': str(config),
                        'rollout': rmanifest, 'reward': {'status': 'planned'},
                        'training': {'status': 'planned'}, 'deployment': None}
            write_json(rounds / 'manifest.json', manifest)
            return manifest
        reward_file = rounds / 'rewards.jsonl'
        rew = reward(roll_dir / 'manifest.json', reward_file)
        train_dir = rounds / ('sft' if mode == 'sft' else 'grpo')
        if mode == 'sft':
            # Select eligible rows while preserving the verified reward record.
            rows = [json.loads(line) for line in reward_file.read_text().splitlines() if line.strip()]
            selected = rounds / 'sft-selected.jsonl'
            selected.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows if r.get('eligible')), encoding='utf-8')
            train = prepare_sft(selected, train_dir, trainer_command) if rows and any(r.get('eligible') for r in rows) else {'status': 'no_eligible_samples'}
        else:
            train = prepare_grpo(reward_file, train_dir, trainer_command) if rew.get('eligible', 0) else {'status': 'no_eligible_samples'}
        deployed = None
        checkpoint = train.get('checkpoint') if isinstance(train, dict) else None
        if checkpoint and deploy_command and train.get('training_completed') is True:
            from analog_trace.deploy import reload_checkpoint
            deployed = reload_checkpoint(checkpoint, deploy_command).descriptor()
        training_failed = isinstance(train, dict) and train.get('status') in {'failed', 'no_eligible_samples'}
        manifest = {'status': 'failed' if training_failed else 'completed',
                    'mode': mode, 'prompts': str(prompts), 'config': str(config),
                    'rollout': rmanifest, 'reward': rew, 'training': train,
                    'deployment': deployed}
        write_json(rounds / 'manifest.json', manifest)
        return manifest


def run_round(**kwargs):
    work_dir = kwargs.pop('work_dir')
    return TrainingOrchestrator(work_dir).run(**kwargs)
