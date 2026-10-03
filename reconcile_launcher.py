#!/usr/bin/env python3
"""Persist the child's PID before importing the potentially slow Hermes runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import sys


def main() -> None:
    agent_dir, worker, job_path = sys.argv[1:]
    path = Path(job_path)
    sys.path.append(str(Path(__file__).resolve().parent))
    import reconcile_lease
    job = json.loads(path.read_text(encoding='utf-8'))
    inherited_fd = job.get('lease_fd') if job.get('spawn_pid') == os.getpid() else None
    with reconcile_lease.hold(path, inherited_fd) as lease:
        job.update(spawn_pid=os.getpid(), process_lease=True, lease_fd=lease.fileno())
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
        tmp.replace(path)
        # Only this child writes its job while alive: the parent must not race worker updates.
        sys.path.insert(0, agent_dir)
        import hermes_bootstrap  # noqa: F401 — activate the installed managed runtime
        sys.argv = [worker, job_path]
        runpy.run_path(worker, run_name='__main__')


if __name__ == '__main__':
    main()
