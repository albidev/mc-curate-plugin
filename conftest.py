"""Never let a standalone plugin test reach the user's vault or live BDH."""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_SCRATCH = Path(os.environ.get('CURATE_TEST_SCRATCH', Path.home() / '.hermes/cache/scratch'))
_SCRATCH.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_SCRATCH)
_SANDBOX = Path(tempfile.mkdtemp(prefix='curate-pytest-home-', dir=str(_SCRATCH)))
os.environ['HERMES_HOME'] = str(_SANDBOX)
os.environ['VB_VAULT'] = str(_SANDBOX / 'vault')
os.environ['BDH_API_URL'] = 'http://127.0.0.1:9'
os.environ['CURATE_SIDECAR_URL'] = 'http://127.0.0.1:9'


@pytest.fixture(autouse=True)
def sandbox_home(monkeypatch):
    # Reset after individual tests deliberately change their runtime home.
    monkeypatch.setenv('HERMES_HOME', str(_SANDBOX))
    monkeypatch.setenv('VB_VAULT', str(_SANDBOX / 'vault'))
    monkeypatch.setenv('BDH_API_URL', 'http://127.0.0.1:9')
    monkeypatch.setenv('CURATE_SIDECAR_URL', 'http://127.0.0.1:9')
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
