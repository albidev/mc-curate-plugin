"""Contract tests for the plugin manifest.

The manifest is the plugin's public HTTP surface: the Mission Control loader
registers one handler per declared endpoint and silently skips any handler name
it cannot find in ``endpoints``. A typo or a stale entry therefore disables a
route without failing anything at load time — the route just 404s at runtime.

These tests make that failure mode loud: every declared endpoint must resolve to
a real callable, and every public handler must be declared, so the manifest and
the module cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import endpoints

PLUGIN_DIR = Path(__file__).resolve().parent
MANIFEST = json.loads((PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))

ENDPOINTS = MANIFEST.get("endpoints", [])


def test_manifest_declares_endpoints():
    assert ENDPOINTS, "manifest.json declares no endpoints"


@pytest.mark.parametrize("endpoint", ENDPOINTS, ids=lambda e: f"{e['method']} {e['path']}")
def test_declared_handler_exists_and_is_callable(endpoint):
    handler = getattr(endpoints, endpoint["handler"], None)
    assert handler is not None, (
        f"manifest declares '{endpoint['handler']}' for {endpoint['method']} "
        f"{endpoint['path']} but endpoints.py does not define it"
    )
    assert callable(handler)


def test_endpoint_paths_are_unique_per_method():
    seen = [(e["method"].upper(), e["path"]) for e in ENDPOINTS]
    duplicates = {key for key in seen if seen.count(key) > 1}
    assert not duplicates, f"duplicate manifest endpoints: {duplicates}"


def test_endpoint_paths_are_local_api_relative():
    """Paths are resolved by the loader under /api/local, so they must be
    relative and must not restate the prefix."""
    for endpoint in ENDPOINTS:
        path = endpoint["path"]
        assert path.startswith("/"), f"{path} must be absolute within the API root"
        assert not path.startswith("/api/local"), (
            f"{path} must not include the /api/local prefix — the loader adds it"
        )


def test_every_public_handler_is_declared():
    """Catch handlers reachable from the manifest's intent but never wired up."""
    declared = {e["handler"] for e in ENDPOINTS}
    public = {
        name
        for name in dir(endpoints)
        if name[0].islower()
        and not name.startswith("_")
        and callable(getattr(endpoints, name))
        and getattr(endpoints, name).__module__ == endpoints.__name__
    }
    undeclared = public - declared - {"PluginError"}
    assert not undeclared, (
        f"endpoints.py defines handlers the manifest never declares: {sorted(undeclared)}"
    )


def test_auth_required_on_every_endpoint():
    """Every route mutates or reads vault data; none may be anonymous."""
    for endpoint in ENDPOINTS:
        assert endpoint.get("authRequired") is True, (
            f"{endpoint['method']} {endpoint['path']} does not require auth"
        )
