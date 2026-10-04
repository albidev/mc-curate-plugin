# Scheduled Ask AI

`scheduled_advisor.py` runs the **same advisor worker and per-vault configuration** as Curate's Ask AI button. It generates opinions only: it never approves, rejects, merges or applies a candidate.

## Execution

Run it in the installed **bootstrapped Hermes runtime**, not an arbitrary system Python. The cron entry point should be a thin `.sh` wrapper under the owning profile's `scripts/` directory. Resolve the runtime with `hermes --print-runtime-command`, initialize `hermes_bootstrap`, then run this file. This avoids importing Hermes dependencies into the telemetry interpreter.

```bash
# Read-only inspection; both forms emit JSON.
<runtime-wrapper> /path/to/scheduled_advisor.py --dry-run
<runtime-wrapper> /path/to/scheduled_advisor.py --list

# Advice run, with a process lock and a 45-minute total budget.
<runtime-wrapper> /path/to/scheduled_advisor.py
```

Register one script-only cron (`--no-agent`), for example `0 10,22 * * *`. Set both `--deliver local` and `--failure-deliver local`: no chat, Bot Chat or Discord delivery. In the Notifications plugin policy, classify `curate-ask-ai` as `report` on `completed`/`failed`; its existing cron importer handles the Notification Center. No service restart is required for the runner or a notification policy edit.

## Selection and results

- Iterate candidate-enabled, writable Curate vaults and resolve each vault's advisor configuration.
- Read the actual BDH vault's `.bdh-candidates`, not the legacy file-candidate directory.
- Select `pending_review`/`pre_approved` candidates **without any existing `curator_verdict`**. Existing manual and legacy advice is preserved.
- Exclude local rejection-ledger entries and candidates already being advised.
- Limit each vault to the existing worker's `MAX_BATCH`; process vaults sequentially so their workers do not multiply BDH concurrency.
- Persist advice with `curator_source: scheduled`; candidate status is unchanged.
- Emit compact per-vault approve/merge/reject/error counts. Empty runs are silent; partial failures produce an error report and nonzero exit status.
- A wait timeout does not apply or cancel anything; existing worker state remains inspectable and in-flight IDs are excluded on subsequent runs.

Runtime files live under `$HERMES_HOME/vault-brain/curate-advice/`: `scheduled.lock`, atomic `scheduled-summary.json`, and the ordinary Ask AI job files. The inspection modes do not write or reconcile job state.

## Verification

```bash
python3 -m pytest -q test_scheduled_advisor.py test_advisor.py
```

The plugin's `conftest.py` isolates Hermes home and disables live BDH endpoints. Tests exercise the real advice worker against fixture candidates, verify status preservation, deduplication and the success/failure report contract. Real-provider smoke runs must use copied candidates and must verify the production source is unchanged.
