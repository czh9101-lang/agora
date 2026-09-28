"""Pytest configuration and shared fixtures for Agora tests."""
from __future__ import annotations

import sys
import tempfile
import os
import types
from pathlib import Path

import pytest

# Load the plugin the way Hermes does — as ``hermes_plugins.agora`` with its own
# search path — so the package's relative imports resolve exactly as they do at
# runtime. Putting the plugin root on sys.path instead (as this file used to)
# would shadow core's own ``tools`` package and leave the plugin's cross-package
# relative imports unresolvable.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PKG = "hermes_plugins.agora"

if PKG not in sys.modules:
    if "hermes_plugins" not in sys.modules:
        _ns = types.ModuleType("hermes_plugins")
        _ns.__path__ = []
        sys.modules["hermes_plugins"] = _ns
    _pkg = types.ModuleType(PKG)
    _pkg.__path__ = [str(_PLUGIN_ROOT)]
    _pkg.__package__ = PKG
    sys.modules[PKG] = _pkg


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Monkeypatch motions._agora_db_path to return a path in a temp directory.

    This ensures tests never touch the real ~/.hermes/agora/motions.db.
    """
    from hermes_plugins.agora.agora.storage import motions

    db_path = tmp_path / "motions.db"
    monkeypatch.setattr(motions, "_agora_db_path", lambda: db_path)
    return db_path


@pytest.fixture()
def clean_db(temp_db):
    """Create a fresh DB for each test by ensuring the schema is initialised."""
    from hermes_plugins.agora.agora.storage import motions

    # _connect() calls _init_schema() which creates tables if they don't exist.
    conn = motions._connect()
    conn.close()
    yield temp_db
