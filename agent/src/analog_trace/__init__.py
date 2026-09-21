"""Local, auditable amplifier agent runner; original evaluator is unchanged."""
__version__ = '1.0.0'

from .trajectory import (Trajectory, ClaudeNativeAdapter, adapt_claude_stream,
                         load_native_jsonl, validate_trajectory, assert_valid_trajectory)
from .sft import export_sft, to_sft_example, response_mask, build_sft_labels
from .deploy import Deployment, reload_checkpoint
from .policy import make_metadata, save_metadata, load_metadata, policy_hash
