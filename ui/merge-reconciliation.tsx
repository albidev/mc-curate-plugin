import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react';
import { currentLocale, tr, useT } from './i18n';
import { canConfirmMerge, isReconciliationJob, sameReview, type ReconciliationJob, type ReviewPreview } from './merge-reconciliation-state';

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

export function useReconciliation(preview: ReviewPreview | null, request: Request) {
  const [job, setJob] = useState<ReconciliationJob | null>(null);
  const [checked, setChecked] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = preview ? JSON.stringify([preview.vault_id, preview.candidate.candidate_id, preview.target.node_id,
    preview.candidate_revision, preview.target_revision]) : '';
  const current = useRef(key);
  current.current = key;
  const startController = useRef<AbortController | null>(null);

  useEffect(() => {
    setJob(null); setChecked(false); setError(null); setStarting(false);
    return () => { startController.current?.abort(); startController.current = null; };
  }, [key]);

  const activeId = job && preview && sameReview(preview, job) && ['queued', 'running'].includes(job.status) ? job.job_id : '';
  useEffect(() => {
    if (!activeId || !preview) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await request<unknown>(`/synthesis/reconcile/status?job=${encodeURIComponent(activeId)}`,
          { signal: controller.signal });
        if (controller.signal.aborted || current.current !== key) return;
        if (!isReconciliationJob(next, preview) || next.job_id !== activeId) throw new Error(tr('reconcile.invalidResponse'));
        setJob(next);
        if (['queued', 'running'].includes(next.status)) timer = window.setTimeout(() => void tick(), 1000);
      } catch (cause) {
        if (!controller.signal.aborted && current.current === key) {
          setError(cause instanceof Error ? cause.message : tr('reconcile.failed'));
          setJob(null); setChecked(false); // Retry start reattaches to the server's still-running job.
        }
      }
    };
    timer = window.setTimeout(() => void tick(), 1000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [activeId, key, request]);

  const analyze = async () => {
    if (!preview?.conflict?.required || startController.current || activeId) return;
    const controller = new AbortController();
    startController.current = controller;
    setStarting(true); setError(null); setJob(null); setChecked(false);
    try {
      const next = await request<unknown>('/synthesis/reconcile', {
        method: 'POST', signal: controller.signal, body: JSON.stringify({
          candidate_id: preview.candidate.candidate_id, vault: preview.vault_id, target_node_id: preview.target.node_id,
          candidate_revision: preview.candidate_revision, target_revision: preview.target_revision, locale: currentLocale(),
        }),
      });
      if (controller.signal.aborted || current.current !== key) return;
      if (!isReconciliationJob(next, preview)) throw new Error(tr('reconcile.invalidResponse'));
      setJob(next);
    } catch (cause) {
      if (!controller.signal.aborted && current.current === key) setError(cause instanceof Error ? cause.message : tr('reconcile.failed'));
    } finally {
      if (startController.current === controller) startController.current = null;
      if (!controller.signal.aborted && current.current === key) setStarting(false);
    }
  };

  return { job, checked, setChecked, error, analyze, busy: starting || !!activeId,
    allowed: canConfirmMerge(preview, job, checked) };
}

export function ReconciliationPanel({ preview, review, disabled }: {
  preview: ReviewPreview; review: ReturnType<typeof useReconciliation>; disabled: boolean;
}) {
  const t = useT();
  const assessment = isReconciliationJob(review.job, preview) ? review.job.assessment : null;
  const compatible = review.job?.status === 'done' && assessment?.classification === 'compatible';
  return <section aria-label={t('reconcile.title')} className="mt-4 rounded-xl border border-amber-400/25 bg-amber-400/10 p-3">
    <h3 className="flex items-center gap-2 text-sm font-semibold text-amber-200"><AlertTriangle size={16}/>{t('reconcile.title')}</h3>
    <p className="mt-2 text-xs leading-5 text-text-muted">{t('reconcile.warning')}</p>
    {!!preview.conflict?.signals?.length && <p className="mt-1 break-words text-xs text-amber-200">{t('reconcile.signals', { signals: preview.conflict.signals.join(', ') })}</p>}
    <button type="button" disabled={disabled || review.busy} onClick={() => void review.analyze()}
      className="mt-3 inline-flex min-h-11 items-center gap-2 rounded-lg border border-amber-400/30 px-3 py-2 text-sm text-amber-100 disabled:opacity-40">
      {review.busy ? <Loader2 size={14} className="animate-spin"/> : <Sparkles size={14}/>}
      {review.busy ? t('reconcile.analyzing') : t('reconcile.analyze')}
    </button>
    {(review.error || review.job?.error) && <p role="alert" className="mt-2 break-words text-xs text-rose-200">{review.error || review.job?.error}</p>}
    {assessment && <div className="mt-3 space-y-2 text-xs leading-5">
      <p className={compatible ? 'font-semibold text-emerald-200' : 'font-semibold text-amber-200'}>
        {t(`reconcile.${assessment.classification}`)} · {assessment.provider}/{assessment.model}</p>
      <p className="break-words text-text">{assessment.reason}</p>
      <div className="grid min-w-0 gap-2 sm:grid-cols-2">
        <div><p className="text-text-muted">{t('reconcile.candidateQuote')}</p><blockquote className="whitespace-pre-wrap break-words rounded-lg bg-black/15 p-2 text-text">{assessment.candidate_quote}</blockquote></div>
        <div><p className="text-text-muted">{t('reconcile.targetQuote')}</p><blockquote className="whitespace-pre-wrap break-words rounded-lg bg-black/15 p-2 text-text">{assessment.target_quote}</blockquote></div>
      </div>
      {compatible ? <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-emerald-400/20 p-3 text-text">
        <input type="checkbox" className="mt-1 shrink-0" checked={review.checked} disabled={disabled} onChange={event => review.setChecked(event.target.checked)}/>
        <span>{t('reconcile.humanConfirm')}</span>
      </label> : <p className="text-amber-200">{t('reconcile.blocked')}</p>}
    </div>}
  </section>;
}
