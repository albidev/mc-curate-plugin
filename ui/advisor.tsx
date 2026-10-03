import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Brain, Loader2 } from 'lucide-react';
import { currentLocale, tr, useT, type Translate } from './i18n';
import type { MessageKey } from './messages';

/**
 * Curate AI advisor — live approve / merge / reject opinions.
 *
 * The backend runs one worker per request and rewrites a job file after every
 * candidate; this hook polls it while a job is active and hands each finished
 * opinion to the route as soon as it lands, so cards flip one by one.
 */

export type AdviceState = 'queued' | 'running' | 'done' | 'error' | 'skipped';
export type Verdict = 'approve' | 'merge' | 'reject';

export interface AdviceItem {
  state: AdviceState;
  verdict?: Verdict;
  confidence?: number;
  reason?: string;
  merge_target?: string | null;
  merge_target_title?: string | null;
  model?: string;
  context_mode?: 'semantic' | 'title_fallback';
  error?: string;
  finished_at?: string;
}

export interface AdviceJob {
  job_id: string;
  vault: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  provider: string;
  model: string;
  total: number;
  completed: number;
  created_at: string;
  order: string[];
  items: Record<string, AdviceItem>;
  skipped?: Record<string, string>;
  error?: string;
}

export interface AdvisorInfo { configured: boolean; provider?: string; model?: string; detail?: string }

export interface CuratorFields {
  curator_verdict?: string;
  curator_confidence?: string;
  curator_note?: string;
  curator_merge_target?: string;
  curator_model?: string;
  curator_source?: string;
  curator_reviewed_at?: string;
}

type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

const POLL_MS = 1000;
const isActive = (job: AdviceJob) => job.status === 'queued' || job.status === 'running';
const isBusy = (item?: AdviceItem) => item?.state === 'queued' || item?.state === 'running';

/** Candidate fields an opinion maps to — the same `extra.curator_*` contract the worker persisted. */
export function adviceToFields(item: AdviceItem): CuratorFields {
  return {
    curator_verdict: item.verdict,
    curator_confidence: item.confidence !== undefined ? item.confidence.toFixed(2) : undefined,
    curator_note: item.reason,
    curator_merge_target: item.merge_target || undefined,
    curator_model: item.model,
    curator_source: 'on_demand',
    curator_reviewed_at: item.finished_at,
  };
}

export function useCuratorAdvisor({ vault, request, enabled, onAdvice }: {
  vault: string;
  request: Request;
  enabled: boolean;
  onAdvice: (candidateId: string, item: AdviceItem) => void;
}) {
  const [jobs, setJobs] = useState<Record<string, AdviceJob>>({});
  const [advisor, setAdvisor] = useState<AdvisorInfo | null>(null);
  const delivered = useRef(new Set<string>());
  const onAdviceRef = useRef(onAdvice);
  onAdviceRef.current = onAdvice;

  // Resume jobs that are still running (page reload, vault switch, other tab).
  useEffect(() => {
    setJobs({});
    setAdvisor(null);
    if (!enabled || !vault) return;
    let cancelled = false;
    request<{ jobs: AdviceJob[]; advisor: AdvisorInfo }>(`/curate/advise/active?vault=${encodeURIComponent(vault)}`)
      .then((data) => {
        if (cancelled) return;
        setAdvisor(data.advisor);
        setJobs(Object.fromEntries((data.jobs || []).map((job) => [job.job_id, job])));
      })
      .catch(() => { if (!cancelled) setAdvisor({ configured: false, detail: tr('advisor.unavailable') }); });
    return () => { cancelled = true; };
  }, [enabled, request, vault]);

  const activeKey = Object.values(jobs).filter(isActive).map((job) => job.job_id).sort().join(',');
  useEffect(() => {
    if (!activeKey) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      const ids = activeKey.split(',');
      const results = await Promise.allSettled(ids.map((id) => request<AdviceJob>(`/curate/advise/status?job=${encodeURIComponent(id)}`)));
      if (stopped) return;
      setJobs((current) => {
        const next = { ...current };
        results.forEach((result) => { if (result.status === 'fulfilled') next[result.value.job_id] = result.value; });
        return next;
      });
      timer = window.setTimeout(() => void tick(), POLL_MS);
    };
    timer = window.setTimeout(() => void tick(), POLL_MS);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [activeKey, request]);

  useEffect(() => {
    for (const job of Object.values(jobs)) {
      for (const [cid, item] of Object.entries(job.items || {})) {
        const key = `${job.job_id}:${cid}`;
        if (item.state === 'done' && !delivered.current.has(key)) {
          delivered.current.add(key);
          onAdviceRef.current(cid, item);
        }
      }
    }
  }, [jobs]);

  const ask = useCallback(async (candidateIds: string[]) => {
    const job = await request<AdviceJob>('/curate/advise', {
      method: 'POST',
      body: JSON.stringify({ vault, candidate_ids: candidateIds }),
    });
    setJobs((current) => ({ ...current, [job.job_id]: job }));
    return job;
  }, [request, vault]);

  /** Newest job wins per candidate, so a re-ask replaces an older result. */
  const liveById = useMemo(() => {
    const out = new Map<string, AdviceItem>();
    Object.values(jobs)
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
      .forEach((job) => Object.entries(job.items || {}).forEach(([cid, item]) => out.set(cid, item)));
    return out;
  }, [jobs]);

  const progress = useMemo(() => {
    const active = Object.values(jobs).filter(isActive);
    return active.length
      ? { total: active.reduce((n, j) => n + j.total, 0), completed: active.reduce((n, j) => n + (j.completed || 0), 0) }
      : null;
  }, [jobs]);

  const failures = useMemo(() => Object.values(jobs).filter((job) => job.status === 'failed' && job.error).map((job) => job.error as string), [jobs]);

  return { ask, liveById, progress, advisor, failures, isBusy: (cid: string) => isBusy(liveById.get(cid)) };
}

const VERDICT_TONE: Record<string, string> = {
  approve: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
  merge: 'border-sky-400/30 bg-sky-400/10 text-sky-200',
  reject: 'border-rose-400/30 bg-rose-400/10 text-rose-200',
};

function targetLabel(target?: string | null, title?: string | null): string {
  if (title) return title;
  if (!target) return '';
  return target.split('/').pop()?.replace(/\.md$/, '') || target;
}

function sourceLabel(fields: CuratorFields, t: Translate): string {
  return fields.curator_source === 'on_demand' ? t('advisor.onDemand') : t('advisor.scheduled');
}

export function verdictLabel(verdict: string, t: Translate): string {
  return ['approve', 'merge', 'reject'].includes(verdict) ? t(`verdict.${verdict}` as MessageKey) : verdict;
}

/** Compact verdict pill for cards. Live state (queued/running/error) wins over the persisted opinion. */
export function CuratorBadge({ fields, live }: { fields: CuratorFields; live?: AdviceItem }) {
  const t = useT();
  if (isBusy(live)) {
    return <span className="inline-flex items-center gap-1 rounded-md border border-violet-400/30 bg-violet-400/10 px-1.5 py-0.5 text-[10px] font-medium text-violet-200" data-curator-badge="busy">
      <Loader2 size={10} className="animate-spin" /> {live?.state === 'queued' ? t('advisor.queued') : t('advisor.thinkingBadge')}
    </span>;
  }
  if (live?.state === 'error') {
    return <span title={live.error} className="rounded-md border border-rose-400/30 bg-rose-400/10 px-1.5 py-0.5 text-[10px] font-medium text-rose-200" data-curator-badge="error">{t('advisor.errorBadge')}</span>;
  }
  const verdict = fields.curator_verdict;
  if (!verdict) return null;
  const confidence = fields.curator_confidence !== undefined && fields.curator_confidence !== '' ? Number(fields.curator_confidence) : null;
  const target = verdict === 'merge' ? targetLabel(fields.curator_merge_target) : '';
  const tooltip = [fields.curator_note, `${sourceLabel(fields, t)}${fields.curator_model ? ` · ${fields.curator_model}` : ''}`].filter(Boolean).join('\n');
  return <span title={tooltip} data-curator-badge={verdict}
    className={`inline-flex max-w-[260px] items-center gap-1 truncate rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${VERDICT_TONE[verdict] || 'border-white/10 bg-white/[0.04] text-text-muted'}`}>
    <Brain size={10} className="shrink-0" />
    <span className="truncate">{t('advisor.badge', { verdict: verdictLabel(verdict, t) })}{target ? ` → ${target}` : ''}{confidence !== null && Number.isFinite(confidence) ? ` ${Math.round(confidence * 100)}%` : ''}</span>
  </span>;
}

/** Full opinion panel for the candidate detail dialog. */
export function CuratorAdvicePanel({ fields, live, canAsk, onAsk }: {
  fields: CuratorFields;
  live?: AdviceItem;
  canAsk: boolean;
  onAsk: () => void;
}) {
  const t = useT();
  const busy = isBusy(live);
  const verdict = fields.curator_verdict;
  return <div className="mt-5 rounded-xl border border-violet-400/20 bg-violet-400/[0.05] p-3" data-curator-panel>
    <div className="flex items-center justify-between gap-3">
      <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-violet-200/80"><Brain size={12} /> {t('advisor.panelTitle')}</p>
      {canAsk && <button type="button" disabled={busy} onClick={onAsk}
        className="inline-flex items-center gap-1.5 rounded-lg border border-violet-400/30 bg-violet-400/10 px-2.5 py-1 text-xs font-medium text-violet-100 hover:bg-violet-400/20 disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Brain size={12} />} {busy ? t('advisor.thinking') : verdict ? t('advisor.askAgain') : t('advisor.ask')}
      </button>}
    </div>
    {busy ? <p className="mt-2 text-xs text-text-muted">{t('advisor.reading')}</p>
      : live?.state === 'error' ? <p className="mt-2 text-xs text-rose-200">{live.error}</p>
      : verdict ? <div className="mt-2 space-y-1.5">
        <CuratorBadge fields={fields} />
        {verdict === 'merge' && fields.curator_merge_target && <p className="break-all text-xs text-sky-200">{t('advisor.target', { target: fields.curator_merge_target })}</p>}
        {fields.curator_note && <p className="text-sm leading-6 text-text">{fields.curator_note}</p>}
        <p className="text-[11px] text-text-subtle">
          {sourceLabel(fields, t)}{fields.curator_model ? ` · ${fields.curator_model}` : ''}{fields.curator_reviewed_at ? ` · ${new Date(fields.curator_reviewed_at).toLocaleString(currentLocale() === 'it' ? 'it-IT' : 'en-US')}` : ''}
          {live?.context_mode === 'title_fallback' ? t('advisor.titleFallback') : ''}
        </p>
      </div>
      : <p className="mt-2 text-xs text-text-muted">{t('advisor.none')}</p>}
  </div>;
}
