"""Checkpoint reload/deployment adapters for local or third-party inference servers."""
from __future__ import annotations
import json
import os
import signal
import subprocess
from pathlib import Path

def _load_policy_metadata(checkpoint):
    try:
        from analog_rl.policy import load_policy_metadata
    except ImportError as exc:  # pragma: no cover - deployment requires RL package
        raise ImportError("analog_rl.policy is required for checkpoint deployment") from exc
    return load_policy_metadata(checkpoint)


class Deployment:
    def __init__(self, checkpoint, command=None, env=None, process=None):
        self.checkpoint = str(Path(checkpoint).resolve())
        self.command = command
        self.env = env or {}
        self.process = process

    @classmethod
    def start(cls, checkpoint, command=None, env=None, cwd=None):
        """Start an inference command, replacing ``{checkpoint}`` in argv.

        With no command this only validates and returns a reload descriptor,
        which is useful for hosted API deployments.
        """
        meta = _load_policy_metadata(checkpoint)
        metadata = meta.to_dict()
        if command is None:
            return cls(checkpoint, env={'POLICY_VERSION': metadata['policy_version'], **(env or {})})
        argv = [str(x).replace('{checkpoint}', str(Path(checkpoint).resolve())) for x in command]
        child_env = os.environ.copy(); child_env.update(env or {})
        child_env['POLICY_VERSION'] = metadata['policy_version']
        proc = subprocess.Popen(argv, cwd=cwd, env=child_env, start_new_session=(os.name == 'posix'))
        return cls(checkpoint, argv, child_env, proc)

    def descriptor(self):
        meta = _load_policy_metadata(self.checkpoint)
        return {'checkpoint': self.checkpoint, 'policy_version': meta.policy_version,
                'model': meta.model, 'command': self.command, 'running': bool(self.process and self.process.poll() is None)}

    def stop(self):
        if not self.process or self.process.poll() is not None:
            return
        if os.name == 'posix':
            os.killpg(self.process.pid, signal.SIGTERM)
        else:
            self.process.terminate()
        self.process.wait(timeout=10)


def reload_checkpoint(checkpoint, command=None, env=None, cwd=None):
    return Deployment.start(checkpoint, command, env, cwd)
