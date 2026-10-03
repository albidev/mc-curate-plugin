/** A model opinion never grants authority; its exact review must match the human's preview. */
export interface ReviewPreview {
  vault_id: string;
  candidate: { candidate_id: string; definition: string };
  target: { node_id: string; content: string };
  candidate_revision: string;
  target_revision: string;
  conflict?: { required: boolean; signals?: string[]; provenance_flag?: boolean };
}
export interface ReconciliationJob {
  job_id: string; status: 'queued' | 'running' | 'done' | 'failed'; vault: string;
  candidate_id: string; target_node_id: string; candidate_revision: string; target_revision: string;
  error?: string; reconciliation_id?: string;
  assessment?: { classification: 'compatible' | 'conflicting' | 'uncertain'; reason: string;
    candidate_quote: string; target_quote: string; provider: string; model: string };
}

export function sameReview(preview: ReviewPreview, job: ReconciliationJob): boolean {
  return job.vault === preview.vault_id && job.candidate_id === preview.candidate.candidate_id
    && job.target_node_id === preview.target.node_id && job.candidate_revision === preview.candidate_revision
    && job.target_revision === preview.target_revision;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function boundedText(value: unknown, max: number): value is string {
  return typeof value === 'string' && !!value.trim() && value.length <= max;
}

export function isReviewPreview(value: unknown): value is ReviewPreview {
  if (!isRecord(value) || typeof value.vault_id !== 'string' || !value.vault_id
    || !isRecord(value.candidate) || typeof value.candidate.candidate_id !== 'string' || !value.candidate.candidate_id
    || typeof value.candidate.definition !== 'string' || !isRecord(value.target)
    || typeof value.target.node_id !== 'string' || !value.target.node_id || typeof value.target.content !== 'string'
    || typeof value.candidate_revision !== 'string' || !/^[0-9a-f]{64}$/.test(value.candidate_revision)
    || typeof value.target_revision !== 'string' || !/^[0-9a-f]{64}$/.test(value.target_revision)) return false;
  // Older BDH previews may omit conflict metadata; malformed present metadata is never legacy.
  if (value.conflict === undefined) return true;
  if (!isRecord(value.conflict) || typeof value.conflict.required !== 'boolean') return false;
  const { signals, provenance_flag } = value.conflict;
  return (signals === undefined || (Array.isArray(signals) && signals.every(signal => typeof signal === 'string')))
    && (provenance_flag === undefined || typeof provenance_flag === 'boolean');
}

/** HTTP generic types do not validate data: fail closed before storing or rendering an opinion. */
export function isReconciliationJob(value: unknown, preview: ReviewPreview): value is ReconciliationJob {
  if (!isReviewPreview(preview) || !isRecord(value) || !boundedText(value.job_id, 20) || !/^rcl-[0-9a-f]{16}$/.test(value.job_id)) return false;
  if (typeof value.status !== 'string' || !['queued', 'running', 'done', 'failed'].includes(value.status)) return false;
  if (value.vault !== preview.vault_id || value.candidate_id !== preview.candidate.candidate_id
    || value.target_node_id !== preview.target.node_id || value.candidate_revision !== preview.candidate_revision
    || value.target_revision !== preview.target_revision) return false;
  if (value.error !== undefined && !boundedText(value.error, 1000)) return false;
  if (value.status !== 'done') return value.assessment === undefined && value.reconciliation_id === undefined;
  if (!boundedText(value.reconciliation_id, 36) || !/^rec-[0-9a-f]{32}$/.test(value.reconciliation_id)) return false;
  const assessment = value.assessment;
  if (!isRecord(assessment) || typeof assessment.classification !== 'string'
    || !['compatible', 'conflicting', 'uncertain'].includes(assessment.classification)) return false;
  return boundedText(assessment.reason, 1000)
    && boundedText(assessment.provider, 200) && boundedText(assessment.model, 200)
    && boundedText(assessment.candidate_quote, 1200) && preview.candidate.definition.includes(assessment.candidate_quote)
    && boundedText(assessment.target_quote, 1200) && preview.target.content.includes(assessment.target_quote);
}

export function canConfirmMerge(preview: ReviewPreview | null, job: ReconciliationJob | null, checked: boolean): boolean {
  if (!isReviewPreview(preview)) return false;
  if (!preview.conflict?.required) return true;
  return isReconciliationJob(job, preview) && job.status === 'done' && checked
    && job.assessment?.classification === 'compatible';
}
