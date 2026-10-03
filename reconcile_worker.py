#!/usr/bin/env python3
"""Detached Hermes-runtime comparison worker. Only registers evidence, never merges."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.append(str(_HERE))

from advisor_worker import atomic_write_json, _now
import reconcile_prompt


def run(path: Path, complete=None, register=None) -> dict:
    job = json.loads(path.read_text(encoding='utf-8'))
    job.update(status='running', pid=os.getpid(), started_at=_now())
    atomic_write_json(path, job)
    try:
        if complete is None:
            from agent.auxiliary_client import call_llm

            def call_model(messages):
                result = call_llm(provider=job['provider'], model=job['model'], messages=messages,
                                  temperature=0, max_tokens=1200, timeout=90)
                return result.choices[0].message.content or ''

            complete = call_model

        messages = reconcile_prompt.messages(job['preview'], job['language'])
        assessment = reconcile_prompt.parse_assessment(complete(messages), job['preview'])
        assessment.update(provider=job['provider'], model=job['model'])
        payload = {**job['identity'], 'target_node_id': job['target_node_id'],
                   'candidate_revision': job['candidate_revision'], 'target_revision': job['target_revision'],
                   'assessment': assessment}
        if register is None:
            def store_assessment(payload):
                request = urllib.request.Request(job['bdh_url'].rstrip('/') + '/api/synthesis/merge-reconciliation',
                    data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)

            register = store_assessment

        record = register(payload)  # BDH revalidates the full texts and BOTH hashes under its lock.
        rid = record.get('reconciliation_id')
        if not isinstance(rid, str) or not rid:
            raise ValueError('BDH did not register the assessment. Merge stays blocked.')
        job.update(status='done', reconciliation_id=rid, assessment=assessment, finished_at=_now())
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            detail += ': ' + exc.read(4096).decode('utf-8', errors='replace')
        job.update(status='failed', error=detail[:1000], finished_at=_now())
    atomic_write_json(path, job)
    return job


if __name__ == '__main__':
    raise SystemExit(0 if run(Path(sys.argv[1]))['status'] == 'done' else 1)
