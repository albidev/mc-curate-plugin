# Mission Control Curate Plugin

Curate is the external Mission Control plugin for reviewing BDH session-synthesis and legacy candidate proposals.

The plugin owns the complete feature:

- candidate discovery and parsing;
- vault-aware approve/reject operations;
- source-note enrichment from the Hermes vault;
- the Curate review UI;
- the Overview `Attention` contribution;
- plugin-owned tests and release history.

Mission Control provides only the host runtime. It does not contain Curate-specific API helpers, routes, or business logic.

Curate integrates two public projects:

- [Hermes Mission Control](https://github.com/albidev/hermes-mission-control) — the host dashboard and plugin runtime;
- [BDH Graph Harness](https://github.com/albidev/bdh-graph-harness) — the graph-backed knowledge and nightly-brain pipeline that produces review candidates.

Curate is installed as an external plugin by cloning this repository into the Hermes plugin directory. The plugin repository itself is intentionally distributed separately from the host and the BDH runtime.

## Install

Curate is installed by cloning this repository into the Hermes external plugin directory:

```bash
mkdir -p ~/.hermes/mc-plugins
git clone https://github.com/albidev/mc-curate-plugin.git \
  ~/.hermes/mc-plugins/curate
```

If the directory already exists:

```bash
cd ~/.hermes/mc-plugins/curate
git pull --ff-only origin main
```

Then link the plugin UI into Mission Control:

```bash
cd /path/to/hermes-mission-control
bash scripts/setup-plugins.sh
```

Restart the Mission Control telemetry sidecar and Vite after installation or update:

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.mission-control-telemetry
launchctl kickstart -k gui/$(id -u)/ai.hermes.mission-control
```

The plugin is active when its directory contains a valid `manifest.json`. There is no Curate-specific environment variable.

## Repository layout

```text
mc-curate-plugin/
├── manifest.json       # Backend plugin manifest and endpoint declarations
├── endpoints.py        # HTTP adapter functions
├── handlers.py         # Candidate business logic and source enrichment
├── ui/
│   ├── CurateRoute.tsx # Review queue, filters, modal, approve/reject UX
│   ├── pagination.ts   # Client-side candidate queue pagination
│   ├── attention.tsx   # Overview Attention contributor
│   ├── manifest.ts     # Frontend manifest
│   ├── route.ts        # Frontend route export
│   └── types.ts        # UI-local manifest types
└── README.md
```

`__pycache__/` and other generated files are local runtime artifacts and must not be committed.

## Backend contract

### Manifest

`manifest.json` declares the plugin identity, route, navigation item, attention surface, and API endpoints:

```json
{
  "id": "curate",
  "name": "Curate",
  "description": "Review BDH session-synthesis and legacy candidate proposals",
  "version": "1.2.0",
  "enabled": true,
  "routePath": "/curate",
  "navItem": {
    "to": "/curate",
    "label": "nav.curate",
    "icon": "ClipboardCheck",
    "order": 60,
    "indicator": {
      "endpoint": "/curate/status",
      "pollMs": 30000,
      "tones": ["neutral", "info", "success", "warning", "error"]
    }
  },
  "endpoints": [
    { "method": "GET", "path": "/curate/status", "handler": "curateStatus", "authRequired": true },
    { "method": "GET", "path": "/candidates", "handler": "listCandidates", "authRequired": true },
    { "method": "GET", "path": "/candidates/vaults", "handler": "listVaults", "authRequired": true },
    { "method": "POST", "path": "/candidates/approve", "handler": "approveCandidate", "authRequired": true },
    { "method": "POST", "path": "/candidates/reject", "handler": "rejectCandidate", "authRequired": true }
  ]
}
```

Backend endpoint paths are relative to `/api/local`:

| Method | Plugin path | Full path | Purpose |
|---|---|---|---|
| GET | `/curate/status` | `/api/local/curate/status` | Generic sidebar indicator state; active when candidates await review |
| GET | `/candidates` | `/api/local/candidates` | List candidates, optionally filtered by vault/status |
| GET | `/candidates/vaults` | `/api/local/candidates/vaults` | List configured vaults and counts |
| POST | `/candidates/approve` | `/api/local/candidates/approve` | Approve: session-synthesis candidates apply immediately; legacy file candidates enter quarantine |
| POST | `/candidates/reject` | `/api/local/candidates/reject` | Reject a candidate with human feedback |
| GET | `/synthesis/merge-targets` | `/api/local/synthesis/merge-targets` | Search writable notes in the candidate vault |
| POST | `/synthesis/merge-preview` | `/api/local/synthesis/merge-preview` | Read-only target/evidence preview and revision hashes |
| POST | `/synthesis/merge` | `/api/local/synthesis/merge` | Explicitly confirmed, revision-bound directed merge |

When `/candidates` is called without a `vault` query parameter, Curate asks
BDH to resolve its configured default vault and returns that resolved ID in the
`vault` field. `/candidates/vaults` returns the same dynamic default as
`default_vault`. Curate never assumes that a vault named `core` exists.

The Mission Control telemetry process must expose the BDH endpoint through its
runtime environment, for example:

```text
BDH_API_URL=http://127.0.0.1:<bdh-api-port>
```

The BDH API port is separate from the Mission Control telemetry port. Configure
both processes with their respective endpoints. If BDH is unavailable or
rejects a vault, Curate returns an error envelope; it does not turn the
failure into an empty candidate list.

### Candidate response

A legacy file candidate normally contains:

```json
{
  "id": "20260904-114041-02",
  "type": "pattern",
  "title": "Cursor-Persisted Batch Resumability",
  "status": "promoted",
  "created": "2026-09-04",
  "confidence": "high",
  "tags": ["orchestration", "state-management", "llm"],
  "sources": ["code-managed-cursor.md", "external-code-driven-chunk-loop.md"],
  "description": null,
  "body": "...",
  "sourceNotes": [
    {
      "source": "code-managed-cursor.md",
      "found": true,
      "title": "Code-managed cursor",
      "path": "$HERMES_VAULT/wiki/concepts/code-managed-cursor.md",
      "body": "..."
    }
  ]
}
```

`sourceNotes` is plugin-enriched data. The candidate generator may emit only metadata and source references; Curate resolves those references inside the configured Hermes vault and exposes bounded source content for review.

The plugin must never resolve arbitrary paths outside the configured vault root.

### Approval semantics and candidate states

Curate uses the same `/candidates/approve` action for two different candidate sources, and the backend branches by source:

- **BDH `session_synthesis` candidates:** Curate records approval and immediately calls BDH `/api/synthesis/apply`. A successful apply creates a note, merges into an existing note, or returns `noop` if the information is already present. The note is written under the selected vault's configured `neurogenesis_dir`; this path does **not** use quarantine.
- **Legacy file candidates:** Curate writes `status: approved` and `quarantine_until` to the candidate file. A separate promoter writes it to the target vault after quarantine. That promoter is currently retired, so this legacy path will remain quarantined until a promoter is enabled again.

The following states describe the legacy file-candidate lifecycle:

| State | Meaning |
|---|---|
| `pending` | Waiting for human review |
| `approved` | Human approved; quarantine is active |
| `rejected` | Human rejected; feedback is stored for future brain runs |
| `promoted` | Quarantine elapsed and the candidate was written to the target vault |
| `modified` | Candidate was edited before approval |

For `session_synthesis`, apply outcomes are `created`, `merged`, `noop`, `conflict`, or `failed`; only `created` and `merged` write or update a vault note. The operation is bound to the candidate's `vault_id` and rejects a vault mismatch. Curate only exposes vaults configured as candidate-enabled; apply resolves the chosen vault's own root and `neurogenesis_dir`.

Approval/rejection is vault-aware. A read-only or non-candidate vault cannot accept mutations.

### Directed merge (human-confirmed)

**Approve** retains automatic destination selection. **Merge into…** selects an
existing note in the same vault and appends the candidate's evidence. **Reject**
records feedback. Legacy file candidates retain their existing actions.

Open a candidate, choose **Merge into…**, search/select a target note, inspect the
existing content and evidence to append, then click **Confirm merge**. The same
flow is available from the card's merge icon. A cluster merge applies only to its
representative candidate, not every sibling.

A candidate's `curator_merge_target` is a suggestion, never a writable browser
path. BDH resolves it literally within the candidate's vault. Missing/ambiguous
suggestions require an explicit selection; there is no fallback to creating a
new note. The search returns at most 50 matches and tells the user to refine it
when more exist.

The plugin forwards `candidate_id`, `vault`, and `target_node_id`. Confirmation
also sends `candidate_revision`, `target_revision`, and the literal boolean
`confirmed: true`. The plugin obtains synthesis/session/source correlation from
the stored candidate, ignoring browser-supplied replacements. BDH revalidates
ownership, target safety, revisions and confirmation before writing.

A targeted candidate cannot use automatic Approve: Curate opens the merge dialog,
and the backend returns 409 to callers trying to bypass it. A successful merge
closes the dialog and refreshes the queue. Stale preview errors keep the dialog
open and require a fresh preview plus another human confirmation; duplicate
clicks send one request. Cancel, Escape, searching and previewing never mutate.
`noop` means evidence was already present and no note changed. Real merges carry
a reversible operation ID and provenance in BDH's audit/journal.

This feature requires matching merge endpoints in BDH. Older BDH installations
return an error; Curate never replaces an unavailable merge with Approve/create.
After an approved deployment, reload BDH and the MC telemetry plugin loader.
Development verification must not restart either live service.

## UI contract

### Route

`ui/route.ts` exports `CuratePlugin`. The route is `/curate`.

The UI provides:

- vault selector;
- summary cards for pending, approved/promoted, total, and confidence;
- candidate search by title, body, tags, or ID;
- status and sort filters;
- paginated candidate queue and auto-rejected audit list (25 review cards/candidates per page);
- compact candidate cards;
- tap/click-only detail modal;
- full candidate description when available;
- a clear metadata-only state when the candidate has no description;
- source-note evidence with full bounded source content;
- Approve, Merge into… (session synthesis), and Reject actions for pending candidates;
- reject feedback modal;
- responsive desktop and mobile layout.

Details are never opened automatically. A candidate modal appears only after an explicit user tap/click and can be closed with:

- close button;
- `Escape`;
- backdrop click;
- `Close` action.

### Clustering and the Jev gate (optional backend)

The UI supports an optional curation pipeline provided by the
[bdh-nightly-consolidation](https://github.com/albidev/bdh-nightly-consolidation)
sidecar (`curate/curate_server.py`, commit `535c185`). **The plugin works fully
without it** — every extended behavior degrades gracefully:

| Backend capability | UI behavior when missing |
|---|---|
| `GET /api/local/candidates/clustered` | Endpoint failure is swallowed; all candidates render as individual cards (the pre-clustering layout). |
| `status: pre_approved` on candidates | The filter option exists but the list is empty; pending cards render as before. |
| `status: auto_rejected` | The "Auto-rejected" filter shows the documented empty state; the Restore button is only reachable from a populated section. |

Conversely, when the sidecar pipeline is enabled the review queue upgrades to:

- **Cluster cards** — similar pending candidates are grouped into one card
  showing a representative, the mean similarity, and the Jev verdict with
  color-coded confidence. A "How this cluster formed" details panel
  (collapsed by default) lists each member with its per-member similarity
  percentage and links to its full detail.
- **Auto-rejected audit section** — candidates filtered automatically by the
  Jev gate (confidence ≥ 80% with verdict `reject`) are listed with their
  verdict, confidence, and timestamp. Each carries a **Restore** button that
  returns the candidate to the review queue via
  `POST /api/local/candidates/restore`; every restore is recorded in the
  candidate frontmatter and feeds the classifier's feedback loop as a
  negative label.

The frontend contains no dependency on any external classifier service. It
only reads optional frontmatter fields (`cluster_id`, `cluster_members`,
`jev_choice`, `jev_confidence`, `jev_criteria_version`) that a backend
without the pipeline never emits.

### Full note versus source evidence

The UI intentionally distinguishes two layers:

1. **Description** — the candidate's own semantic description, when the generator emitted one.
2. **Evidence from source notes** — the original BDH/Hermes notes referenced by `sources`.

If a candidate contains metadata only, the UI says so instead of presenting raw YAML as a fake description. The source-note sections then show the actual evidence used to produce the candidate.

### AI advisor (approve / merge / reject opinions)

You can ask an LLM for an opinion on one candidate (brain icon on a card, or **Ask AI** in the
detail dialog) or in bulk (**Ask AI (N)** in the header). The bulk button counts only the visible
pending session-synthesis candidates the advisor has not answered yet. A scheduled cron opinion
still counts, because it comes from an older model. To ask again about a candidate that already
has an opinion, use its detail dialog.
Badges update live as each opinion lands. The advisor only gives an opinion: it never approves,
merges, or rejects a candidate, and it never changes its status. Applying opinions is a separate,
human step: see **Accepting AI suggestions in bulk** below.

| Route | Purpose |
|---|---|
| `POST /api/local/curate/advise` `{vault, candidate_ids}` | Starts a job and returns the job id. Candidates that are already reviewed, missing, or in progress are listed under `skipped`. |
| `GET /api/local/curate/advise/status?job=` | Live job state. The UI polls it every second while a job is active. |
| `GET /api/local/curate/advise/active?vault=` | Jobs still running plus the configured model, so a page reload picks up running jobs. |

How it works:

- The telemetry process cannot import the Hermes model client. Each job therefore runs
  `advisor_worker.py` as a detached process inside the Hermes runtime, found with
  `hermes --print-runtime-command`. Credentials come from Hermes.
- For each candidate, the worker:
  1. asks BDH `/api/query` with `learn: false` for the 6 closest notes in the same vault. This
     is read-only: no Hebbian update and no neurogenesis. If BDH is down, it falls back to
     `merge-targets` title matches.
  2. sends the model the candidate, those notes, similar pending candidates, the vault's
     **signal / noise profile**, and up to 6 **past decisions by Albi** from the same vault.
     The past decisions are approvals that overruled a curator reject, rejections that overruled a
     curator approve, and rejections with a content reason. Plain agreements are skipped because
     they say nothing about the bar. Rejections justified by provenance (probe artifacts) are skipped
     because the model cannot see provenance. The prompt ranks the past decisions above the
     profile, and the profile above the model's own taste (`advisor_prompt.SYSTEM_PROMPT`).
  3. validates the answer. A merge target has to be one of the notes it was shown, and BDH has
     to accept it as a merge target. If not, the model gets one corrective retry.
  4. writes the opinion into the candidate's `extra.curator_*` fields. These are the same fields
     the scheduled `curate-curator-review` cron writes, with `curator_source: on_demand`,
     `curator_model`, `curator_confidence`, and `curator_reviewed_at` added.
- Job files and logs are kept for 7 days under `$HERMES_HOME/vault-brain/curate-advice/jobs/`.
  The worker is the only process that writes the job file. If the worker dies, the job is shown
  as failed instead of staying stuck on "running".

A `merge` opinion sets `curator_merge_target`. Approve then opens the directed merge dialog,
the same thing that happens after a curator-review merge verdict. A later `approve` or `reject`
opinion removes that target.

The model is chosen per vault in the local `curate-vaults.yaml`. That file is gitignored and
never committed:

```yaml
advisor:
  default: {provider: anthropic, model: claude-sonnet-5}   # every vault without an override
  concurrency: 4
vaults:
  core:
    advisor:
      provider: ollama-cloud
      model: deepseek-v4.1-flash
      description: Core — Hermes Agent, Mission Control, BDH memory system, ...
      signal: [Diagnoses from real incidents on our stack, ...]   # what is signal for us
      noise: [Textbook definitions with nothing specific to us, ...]
```

### Accepting AI suggestions in bulk

When a bulk **Ask AI** finishes, a banner offers **Rivedi e applica**. The **Review AI (N)**
header button opens the same panel at any time. On a phone the panel is a full-screen sheet:
verdict tabs at the top, and the apply button in a footer within thumb reach that respects the
safe-area insets. On desktop it is a modal.

- Each row shows the verdict, the confidence, the title and the reason. A merge row also shows
  the target note. Tap a row to see the claim that will be appended next to the start of the
  target note, or **Decidi a mano** to open the candidate itself.
- Rows are pre-ticked only when the on-demand advisor is at least 80% confident. Cron opinions
  are never pre-ticked. An `approve` that still carries a merge target is blocked, and so is a
  merge without a usable preview.
- **Applica N** runs one background job: rejects first, then approves, then merges grouped by
  note. Closing the panel does not stop it. The header shows `Applico x/y`, and a reload
  re-attaches to the job. Afterwards, approve and merge rows have **Annulla**, which uses BDH's
  operation revert.

| Route | Purpose |
|---|---|
| `POST /api/local/curate/accept/plan` `{vault, candidate_ids}` | Read-only. Returns rows with the current advice, an `advice_token`, and a merge preview (target, claim, revisions) for each merge. |
| `POST /api/local/curate/accept` `{vault, items}` | Applies the ticked rows. Each item carries `id`, `verdict`, `advice_token`, and, for a merge, the previewed `target_node_id` and revisions. Only one batch can run per vault at a time (`409 accept_running`). |
| `GET /api/local/curate/accept/status?job=` | Live state for each row: `queued`, `running`, `done`, `stale`, or `error`. |
| `GET /api/local/curate/accept/active?vault=` | The running batch, if any. |

Guarantees (`accept_jobs.py`, `test_accept.py`):

- **Only what you saw is applied.** Each row is re-read before it is applied. If the advisor
  re-ran or the candidate was decided in the meantime, the `advice_token` no longer matches and
  the row ends as `stale`. Nothing is written for that row.
- **Merges stay revision-checked.** A merge row sends the revisions from the preview, as the
  single-merge dialog does. The only change tolerated is one made by this same batch: after
  merging an earlier row into the same note, the job reads the note's new revision. Any other
  edit to the note makes the row `stale`.
- **The advisor never learns from itself.** A rejection accepted from a suggestion is stored
  with the model's reason and `decided_via: ai_accepted`. `advisor_prompt.precedent_of` skips it.
  The advisor jobs also overlay the local rejection ledger. Curate rejections live only in that
  ledger, so without the overlay the advisor would not see Albi's rejections and their reasons.
- **BDH stalls are retried.** Every vault write triggers a graph rebuild in BDH, and BDH can
  then stop responding for a minute or two. BDH calls are retried with backoff for up to 150 s.
  Retries are safe: approve/apply and directed merge are idempotent for identical requests.
- A row that fails or goes `stale` does not stop the rest of the batch. If the telemetry service
  restarts during a batch, the batch is marked failed and the rows still queued are left as they
  were.

If you disagree with a suggestion, untick the row and decide it by hand. That decision is the
one the advisor learns from.

## Overview Attention surface

Curate contributes an optional generic `attention` surface to Mission Control:

```json
{
  "surfaces": {
    "attention": {
      "enabled": true,
      "order": 60
    }
  }
}
```

`ui/attention.tsx` owns the semantics and data fetching. It:

- queries Curate's own `/candidates/vaults` and `/candidates` endpoints;
- counts pending candidates per vault;
- reports the total to Mission Control through `onActiveChange(count)`;
- renders the Curate attention row and its `/curate` link;
- fails silently when the plugin backend is unavailable;
- refreshes every 30 seconds.

Mission Control only renders generic plugin attention contributors. It does not know Curate, candidate semantics, vault semantics, or Curate endpoints.

## Development

The plugin UI is linked into a local Mission Control checkout with:

```bash
cd /path/to/hermes-mission-control
bash scripts/setup-plugins.sh
```

The symlink is intentionally ignored by MC Git and must never be committed from the host repository.

Run the host with the plugin installed:

```bash
cd /path/to/hermes-mission-control
pnpm build
```

For backend smoke checks:

```bash
TOKEN="$(grep '^MISSION_CONTROL_TOKEN=' /path/to/hermes-mission-control/.env | cut -d= -f2-)"
MC_URL="${MISSION_CONTROL_LOCAL_TELEMETRY_URL:?Set the Mission Control telemetry URL}"
curl -H "Authorization: Bearer ${TOKEN}" \
  "$MC_URL/api/local/plugins"

curl -H "Authorization: Bearer ${TOKEN}" \
  "$MC_URL/api/local/candidates"
```

Expected plugin discovery includes:

```json
{
  "id": "curate",
  "version": "1.0.0"
}
```

### Isolated directed-merge verification

Run the backend suite from the plugin root with a Python environment containing
pytest and PyYAML:

```bash
python3 -m pytest -q --basetemp="${TMPDIR:?Set a scratch directory}/curate-tests"
```

`conftest.py` isolates `HERMES_HOME`, candidate configuration and vault paths, and
disables live BDH/Curate-sidecar URLs. All temporary homes use the Hermes scratch
cache (override with `CURATE_TEST_SCRATCH`). A temporary primary vault alone is
not enough: legacy tests can otherwise load real configured candidate folders.

The browser harness renders the actual `CurateRoute` and intercepts its API
requests. With sibling MC dependencies installed, start an isolated Vite server:

```bash
node ../hermes-mission-control/node_modules/vite/bin/vite.js browser-harness \
  --config browser-harness/vite.config.mjs
```

In another terminal, use a scratch Python venv with Playwright and Chromium:

```bash
python3 browser-harness/verify_merge_browser.py
```

`CURATE_MC_ROOT` overrides the host checkout. `CURATE_UI_TEST_URL` overrides the
loopback harness URL. `CURATE_BROWSER_EXECUTABLE` selects a cached Chromium when
its revision differs from the Playwright package. The checks cover rendered
confirmation, cancel/Escape, missing hints/search, stale revisions, mobile detail
and duplicate clicks. No production API is called.

BDH's `tests/test_curate_directed_merge_integration.py` uses MC's real manifest
loader, real HTTP adapters, and temporary BDH vaults to verify the complete
backend path, including audit, idempotency and revert. Build MC with a scratch
`--outDir` rather than overwriting a serving `dist/`.

## Testing checklist

Before pushing a plugin change:

- `python3 -m py_compile handlers.py endpoints.py`;
- verify candidate listing with an installed vault;
- verify candidate listing with a missing/empty vault;
- verify pagination on the final partial page, after filter/search/sort changes, and with clustered candidates;
- verify `sourceNotes` resolves only notes inside the configured vault;
- verify YAML dates, lists, multiline values, and legacy malformed candidates;
- verify `sourceNotes` does not make the endpoint unreasonably slow;
- verify UI build through MC with the plugin installed;
- verify the detail modal is closed on initial page load;
- verify a tap/click opens the modal;
- verify the full note and source evidence are readable on mobile;
- verify approve/reject actions and error states;
- remove the plugin and confirm MC still builds and starts without it.

Useful local performance check:

```bash
MC_URL="${MISSION_CONTROL_LOCAL_TELEMETRY_URL:?Set the Mission Control telemetry URL}"
time curl -H "Authorization: Bearer ${TOKEN}" \
  "$MC_URL/api/local/candidates" >/tmp/curate.json
```

Source-note indexing and caching should keep the response fast even when the vault contains many Markdown files.

## Troubleshooting

### Curate is missing from the sidebar

```bash
cd /path/to/hermes-mission-control
bash scripts/setup-plugins.sh
launchctl kickstart -k gui/$(id -u)/ai.hermes.mission-control
```

Confirm the UI route is served:

```bash
curl -I http://127.0.0.1:5174/src/plugins/curate/route.ts
```

### Curate page stays in loading

Check the telemetry endpoint directly:

```bash
MC_URL="${MISSION_CONTROL_LOCAL_TELEMETRY_URL:?Set the Mission Control telemetry URL}"
curl -i -H "Authorization: Bearer ${TOKEN}" \
  "$MC_URL/api/local/candidates"
```

If the response is 500, inspect the telemetry error log. Common causes include a stale telemetry process after a plugin update or non-JSON-safe YAML values such as Python `date` objects.

Restart the sidecar:

```bash
launchctl kickstart -k gui/$(id -u)/ai.hermes.mission-control-telemetry
```

### Source notes are missing

Check:

- the candidate's `sources` field;
- the source note exists under the configured Hermes vault root (for example `$HERMES_VAULT`);
- the source uses either a vault-relative path or a resolvable basename;
- the telemetry process was restarted after updating `handlers.py`.

Missing source notes are reported as `found: false`; the plugin does not follow paths outside the vault root.

## Release

Curate is versioned independently from Mission Control. Update the plugin repository, then pull it into the installed directory:

```bash
cd ~/.hermes/mc-plugins/curate
git pull --ff-only origin main
```

Restart telemetry and Vite after updating. The host repository should not need a code change for a normal Curate release.
