"""YAML config cache: parse once per on-disk version, never leak shared state."""
import os

import pytest
import yaml

import handlers


@pytest.fixture(autouse=True)
def _fresh_cache():
    handlers._YAML_FILE_CACHE.clear()
    yield
    handlers._YAML_FILE_CACHE.clear()


def _write(path, text, *, bump_ns=0):
    path.write_text(text, encoding="utf-8")
    if bump_ns:
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump_ns))


def test_cached_yaml_parses_once_per_file_version(tmp_path, monkeypatch):
    cfg = tmp_path / "curate-vaults.yaml"
    _write(cfg, "vaults:\n  core:\n    label: Core\n")
    calls = []
    real = handlers._yaml_safe_load
    monkeypatch.setattr(handlers, "_yaml_safe_load", lambda text: calls.append(1) or real(text))

    for _ in range(5):
        assert handlers._cached_yaml_file(cfg) == {"vaults": {"core": {"label": "Core"}}}

    assert len(calls) == 1


def test_cached_yaml_reloads_when_file_changes(tmp_path):
    cfg = tmp_path / "curate-vaults.yaml"
    _write(cfg, "vaults:\n  core:\n    label: Core\n")
    assert handlers._cached_yaml_file(cfg)["vaults"]["core"]["label"] == "Core"

    _write(cfg, "vaults:\n  core:\n    label: Renamed\n", bump_ns=1_000_000)

    assert handlers._cached_yaml_file(cfg)["vaults"]["core"]["label"] == "Renamed"


def test_cached_yaml_returns_private_copies(tmp_path):
    cfg = tmp_path / "curate-vaults.yaml"
    _write(cfg, "vaults:\n  core:\n    label: Core\n")

    first = handlers._cached_yaml_file(cfg)
    first["vaults"]["core"]["label"] = "mutated"

    assert handlers._cached_yaml_file(cfg)["vaults"]["core"]["label"] == "Core"


def test_cached_yaml_keeps_error_contract(tmp_path):
    cfg = tmp_path / "curate-vaults.yaml"
    _write(cfg, "vaults: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        handlers._cached_yaml_file(cfg)
    with pytest.raises(OSError):
        handlers._cached_yaml_file(tmp_path / "missing.yaml")


def test_read_vaults_config_still_reports_bad_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "curate-vaults.yaml"
    _write(cfg, "vaults: [unclosed\n")
    monkeypatch.setattr(handlers, "_vaults_file", lambda: cfg)

    data, error = handlers.read_vaults_config()

    assert data == {}
    assert error and error.startswith("curate-vaults.yaml could not be read:")


def test_yaml_safe_load_rejects_unsafe_tags():
    with pytest.raises(yaml.YAMLError):
        handlers._yaml_safe_load("!!python/object/apply:os.system ['echo pwned']")


def test_list_candidates_resolves_vault_root_once(tmp_path, monkeypatch):
    candidates_dir = tmp_path / "candidates"
    candidates_dir.mkdir()
    for i in range(4):
        (candidates_dir / f"c{i}.md").write_text(
            f"---\nid: c{i}\nstatus: pending\ntitle: Card {i}\n---\nbody {i}\n", encoding="utf-8",
        )
    monkeypatch.setattr(handlers, "_load_vaults", lambda: {"legacy": {"candidates_dir": str(candidates_dir)}})
    monkeypatch.setattr(handlers, "_load_routing_vaults", lambda: {})
    monkeypatch.setattr(handlers, "_load_bdh_candidates", lambda vault, status: None)
    calls = []
    real = handlers._candidate_vault_root
    monkeypatch.setattr(handlers, "_candidate_vault_root", lambda vault: calls.append(vault) or real(vault))

    result = handlers.list_candidates(vault="legacy")

    assert sorted(c["id"] for c in result) == ["c0", "c1", "c2", "c3"]
    assert calls == ["legacy"]
