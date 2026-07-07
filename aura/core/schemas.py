# aura/core/schemas.py
# DEPRECATED — canonical definitions live in aura.schemas.command.
# This module re-exports for backwards compatibility during migration.
# TODO: remove this file once all imports are repointed.

from aura.schemas.command import CommandPlan, ExecutionResult, ExecutorType  # noqa: F401
