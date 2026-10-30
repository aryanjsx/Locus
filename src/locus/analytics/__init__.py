"""External anonymous usage analytics.

Sends anonymized events to PostHog so the Locus team can measure
setup success, retention, feature usage, and churn — without ever
collecting chat content, prompts, file paths, emails, IPs, or hardware
identifiers.

Distinct from :mod:`locus.telemetry`, which stores local FLOPs and
energy metrics in a SQLite DB and never leaves the machine.

Disable: set ``[analytics] enabled = false`` in ``~/.locus/config.toml``.
"""

from locus.analytics.aggregator import SessionAggregator
from locus.analytics.bridge import EventBridge
from locus.analytics.client import AnalyticsClient
from locus.analytics.identity import (
    get_or_create_anon_id,
    is_analytics_enabled,
    reset_anon_id,
)
from locus.analytics.redaction import hash_id, redact

__all__ = [
    "AnalyticsClient",
    "EventBridge",
    "SessionAggregator",
    "get_or_create_anon_id",
    "is_analytics_enabled",
    "reset_anon_id",
    "redact",
    "hash_id",
]
