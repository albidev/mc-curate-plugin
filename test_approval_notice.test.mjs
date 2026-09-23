import assert from "node:assert/strict";
import test from "node:test";

import { approvalNotice } from "./ui/approval-notice.ts";

test("session_synthesis created result says it was added to the vault", () => {
  assert.equal(
    approvalNotice({ source: "session_synthesis", status: "created" }),
    "Approved and added to the selected vault.",
  );
});

test("session_synthesis merged result says it was merged into the vault", () => {
  assert.equal(
    approvalNotice({ source: "session_synthesis", status: "merged" }),
    "Approved and merged into an existing vault note.",
  );
});

test("session_synthesis conflict is not reported as a successful vault write", () => {
  assert.equal(
    approvalNotice({ source: "session_synthesis", status: "conflict" }),
    "Approval recorded, but vault apply found a conflict; no vault note was created.",
  );
});

test("legacy file candidate reports quarantine rather than immediate apply", () => {
  assert.equal(
    approvalNotice({ status: "approved", quarantine_until: "2026-09-24T00:00:00Z" }),
    "Candidate approved; quarantined, not yet in the vault.",
  );
});

test("synthesis candidate can be identified by its correlation field", () => {
  assert.equal(
    approvalNotice({ synthesis_id: "synthesis-123", status: "noop" }),
    "Approved; the information was already present in the vault.",
  );
});
