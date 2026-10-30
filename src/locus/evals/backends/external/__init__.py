"""External-framework subprocess backends (Hermes Agent, OpenClaw)."""

from locus.evals.backends.external.hermes_agent import HermesBackend
from locus.evals.backends.external.openclaw import OpenClawBackend

__all__ = ["HermesBackend", "OpenClawBackend"]
