"""icalendar-searcher: Search and filter iCalendar components.

This package provides the Searcher class for filtering, sorting, and
expanding calendar components (VEVENT, VTODO, VJOURNAL).
"""

## for python 3.9 support
from __future__ import annotations

from .collation import Collation
from .searcher import Searcher

__all__ = ["Searcher", "Collation"]

# Version is set by hatch-vcs at build time (written to _version.py)
try:
    from importlib.metadata import version

    __version__ = version("icalendar-searcher")
except Exception:
    __version__ = "unknown"
