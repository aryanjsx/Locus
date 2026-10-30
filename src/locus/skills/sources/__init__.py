"""Skill source resolvers — Hermes, OpenClaw, generic GitHub."""

from locus.skills.sources.base import ResolvedSkill, SourceResolver
from locus.skills.sources.github import GitHubResolver
from locus.skills.sources.hermes import HERMES_REPO_URL, HermesResolver
from locus.skills.sources.openclaw import OPENCLAW_REPO_URL, OpenClawResolver

__all__ = [
    "GitHubResolver",
    "HERMES_REPO_URL",
    "HermesResolver",
    "OPENCLAW_REPO_URL",
    "OpenClawResolver",
    "ResolvedSkill",
    "SourceResolver",
]
