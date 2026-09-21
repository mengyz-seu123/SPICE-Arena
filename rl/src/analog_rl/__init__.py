"""Reproducible synthetic-data, rollout, reward and GRPO workflow utilities."""

__version__ = "0.2.0"

# Optional training primitives (all imports are dependency-light).
from .masks import (action_mask, build_action_mask, build_response_mask,
                    build_rl_action_mask, build_sft_response_mask,
                    masked_labels, response_mask)
from .policy import (CheckpointManager, PolicyMetadata, checkpoint_hash,
                     load_policy_metadata, make_policy_metadata,
                     policy_version, save_policy_metadata,
                     verify_policy_metadata)
from .trainer import (GRPOConfig, GRPOTrainer, SFTConfig, SFTTrainer,
                      collate_sft, compute_grpo_loss, grpo_objective,
                      create_trl_sft_trainer, create_trl_grpo_trainer)

__all__ = [
    "action_mask", "build_action_mask", "build_response_mask", "masked_labels",
    "build_rl_action_mask", "build_sft_response_mask", "response_mask", "CheckpointManager", "PolicyMetadata", "checkpoint_hash",
    "load_policy_metadata", "make_policy_metadata", "save_policy_metadata",
    "policy_version", "verify_policy_metadata", "GRPOConfig", "GRPOTrainer", "SFTConfig",
    "SFTTrainer", "collate_sft", "grpo_objective", "compute_grpo_loss",
    "create_trl_sft_trainer", "create_trl_grpo_trainer",
]

from .reward import (MetricSpec, normalize_metric, metric_valid, score_metrics,
                     reward_from_result, compute_reward, calculate_reward, TASK_METRICS)
from .orchestrator import TrainingOrchestrator, run_round

__all__ += ["MetricSpec", "normalize_metric", "metric_valid", "score_metrics", "reward_from_result",
            "compute_reward", "calculate_reward", "TASK_METRICS", "TrainingOrchestrator", "run_round"]
