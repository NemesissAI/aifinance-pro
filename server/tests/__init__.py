"""AIFinance server test package.

Guarantees database isolation by ensuring conftest setup runs before any server modules
connect to SQLite.
"""

from __future__ import annotations
