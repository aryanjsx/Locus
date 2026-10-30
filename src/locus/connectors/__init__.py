"""Data source connectors for Deep Research."""

from locus.connectors._stubs import (
    Attachment,
    BaseConnector,
    Document,
    SyncStatus,
)
from locus.connectors.store import KnowledgeStore

__all__ = ["Attachment", "BaseConnector", "Document", "KnowledgeStore", "SyncStatus"]

# Auto-register built-in connectors
import locus.connectors.obsidian  # noqa: F401

try:
    import locus.connectors.gmail  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.gmail_imap  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.gdrive  # noqa: F401
except ImportError:
    pass  # httpx may not be installed

try:
    import locus.connectors.notion  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.granola  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.gcontacts  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.imessage  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.apple_notes  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.apple_music  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.apple_contacts  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.apple_calendar  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.slack_connector  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.outlook  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.imap  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.gcalendar  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.dropbox  # noqa: F401
except ImportError:
    pass  # httpx may not be installed

try:
    import locus.connectors.whatsapp  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.oura  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.apple_health  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.strava  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.spotify  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.google_tasks  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.weather  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.github_notifications  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.hackernews  # noqa: F401
except ImportError:
    pass

try:
    import locus.connectors.news_rss  # noqa: F401
except ImportError:
    pass
