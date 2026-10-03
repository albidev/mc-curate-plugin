export interface ApprovalCandidateOutcome {
  source?: string;
  synthesis_id?: string;
  status?: string;
  quarantine_until?: string;
}

// Self-contained (no imports) so `node --experimental-strip-types --test` can load it directly.
const MESSAGES = {
  en: {
    legacy: 'Candidate approved; quarantined, not yet in the vault.',
    created: 'Approved and added to the selected vault.',
    merged: 'Approved and merged into an existing vault note.',
    noop: 'Approved; the information was already present in the vault.',
    applied: 'Candidate was already applied to the selected vault.',
    conflict: 'Approval recorded, but vault apply found a conflict; no vault note was created.',
    failed: 'Approval recorded, but vault apply failed; the candidate was not written.',
    other: 'Candidate approved; apply returned status “{status}”.',
    unknown: 'unknown',
  },
  it: {
    legacy: 'Candidata approvata: in quarantena, non ancora nel vault.',
    created: 'Approvata e aggiunta al vault selezionato.',
    merged: 'Approvata e unita a una nota esistente del vault.',
    noop: "Approvata: l'informazione era già presente nel vault.",
    applied: 'La candidata era già stata applicata al vault selezionato.',
    conflict: "Approvazione registrata, ma l'applicazione al vault ha trovato un conflitto: nessuna nota creata.",
    failed: "Approvazione registrata, ma l'applicazione al vault è fallita: la candidata non è stata scritta.",
    other: "Candidata approvata; l'applicazione ha restituito lo stato “{status}”.",
    unknown: 'sconosciuto',
  },
} as const;

type Outcome = 'created' | 'merged' | 'noop' | 'applied' | 'conflict' | 'failed';
const OUTCOMES = new Set<string>(['created', 'merged', 'noop', 'applied', 'conflict', 'failed']);

export function approvalNotice(candidate?: ApprovalCandidateOutcome, locale: 'en' | 'it' = 'en'): string {
  const m = MESSAGES[locale] ?? MESSAGES.en;
  const isSessionSynthesis = candidate?.source === 'session_synthesis' || Boolean(candidate?.synthesis_id);
  if (!isSessionSynthesis) return m.legacy;
  const status = candidate?.status || '';
  if (OUTCOMES.has(status)) return m[status as Outcome];
  return m.other.replace('{status}', status || m.unknown);
}
