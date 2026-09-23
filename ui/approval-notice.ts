export interface ApprovalCandidateOutcome {
  source?: string;
  synthesis_id?: string;
  status?: string;
  quarantine_until?: string;
}

export function approvalNotice(candidate?: ApprovalCandidateOutcome): string {
  const isSessionSynthesis = candidate?.source === 'session_synthesis' || Boolean(candidate?.synthesis_id);
  if (!isSessionSynthesis) return 'Candidate approved; quarantined, not yet in the vault.';

  switch (candidate?.status) {
    case 'created':
      return 'Approved and added to the selected vault.';
    case 'merged':
      return 'Approved and merged into an existing vault note.';
    case 'noop':
      return 'Approved; the information was already present in the vault.';
    case 'applied':
      return 'Candidate was already applied to the selected vault.';
    case 'conflict':
      return 'Approval recorded, but vault apply found a conflict; no vault note was created.';
    case 'failed':
      return 'Approval recorded, but vault apply failed; the candidate was not written.';
    default:
      return `Candidate approved; apply returned status “${candidate?.status || 'unknown'}”.`;
  }
}
