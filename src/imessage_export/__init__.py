"""micap-imessage-export: forensic export of iMessage/SMS conversations.

Reads Apple's Messages database read-only and produces a court-ready PDF
exhibit, a faithful HTML rendering, structured JSON, and a SHA-256 chain-of-
custody manifest.
"""

from .version import TOOL_NAME, __version__

__all__ = ["__version__", "TOOL_NAME"]
