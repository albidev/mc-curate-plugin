import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, CheckCircle2, ChevronDown, ExternalLink, GitMerge, Loader2, Sparkles, Undo2, X, XCircle } from 'lucide-react';
import { useT } from './i18n';
import type { MessageKey } from './messages';

/**
 * Accept AI suggestions in bulk.
 *
 * `plan` asks the server for the queue's current advice (and a merge preview for
 * every merge), the human ticks rows, `start` applies them as one background job.
 * Full-screen sheet on phones (sticky tabs + thumb-reach footer), modal on desktop.
 * Closing never stops a running batch: the toolbar keeps showing its progress.
 */

type Verdict = 'reject' | 'merge' | 'approve';
type RowState = 'queued' | 'running' | 'done' | 'stale' | 'error';
type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

export interface PlanRow {
  id: string;
  title: string;
  definition: string;
  verdict: Verdict;
  confidence: number | null;
  reason: string;
  source: 'on_demand' | 'scheduled';
  model: string;
  advice_token: string;
  preselected: boolean;
  blocked?: string;
  merge?: { target_node_id: string; title: string; note_path: string; target_excerpt: string; claim: string; candidate_revision: string; target_revision: string };
}

export interface AcceptItem { state: RowState; verdict?: Verdict; error?: string; note_path?: string; operation_id?: string; noop?: boolean }
export interface AcceptJob {
  job_id: string; vault: string; status: 'queued' | 'running' | 'done' | 'failed'; total: number; completed: number;
  order: string[]; items: Record<string, AcceptItem>; summary?: Record<'done' | 'stale' | 'error', number>; error?: string;
}

const POLL_MS = 1000;
const isActive = (job?: AcceptJob | null) => !!job && (job.status === 'queued' || job.status === 'running');

const VERDICTS: { id: Verdict; label: MessageKey; picked: MessageKey; icon: typeof XCircle; tone: string; dot: string }[] = [
  { id: 'reject', label: 'accept.verdictReject', picked: 'accept.pickedReject', icon: XCircle, tone: 'text-rose-200 border-rose-400/30 bg-rose-400/10', dot: 'bg-rose-400' },
  { id: 'merge', label: 'accept.verdictMerge', picked: 'accept.pickedMerge', icon: GitMerge, tone: 'text-sky-200 border-sky-400/30 bg-sky-400/10', dot: 'bg-sky-400' },
  { id: 'approve', label: 'accept.verdictApprove', picked: 'accept.pickedApprove', icon: Check, tone: 'text-emerald-200 border-emerald-400/30 bg-emerald-400/10', dot: 'bg-emerald-400' },
];
const VERDICT = Object.fromEntries(VERDICTS.map((v) => [v.id, v])) as Record<Verdict, (typeof VERDICTS)[number]>;

/** Live state of the vault's accept batch; re-attaches after reload or vault switch. */
export function useAcceptJob({ vault, request, enabled, onFinished }: {
  vault: string; request: Request; enabled: boolean; onFinished: (job: AcceptJob) => void;
}) {
  const [job, setJob] = useState<AcceptJob | null>(null);
  const finishedRef = useRef(onFinished);
  finishedRef.current = onFinished;
  const reported = useRef(new Set<string>());

  useEffect(() => {
    setJob(null);
    if (!enabled || !vault) return;
    let cancelled = false;
    request<{ jobs: AcceptJob[] }>(`/curate/accept/active?vault=${encodeURIComponent(vault)}`)
      .then((data) => { if (!cancelled && data.jobs?.length) setJob(data.jobs[data.jobs.length - 1]); })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [enabled, request, vault]);

  const activeId = isActive(job) ? job!.job_id : '';
  useEffect(() => {
    if (!activeId) return;
    let stopped = false;
    let timer: number | undefined;
    const tick = async () => {
      try {
        const next = await request<AcceptJob>(`/curate/accept/status?job=${encodeURIComponent(activeId)}`);
        if (stopped) return;
        setJob(next);
        if (isActive(next)) timer = window.setTimeout(() => void tick(), POLL_MS);
      } catch {
        if (!stopped) timer = window.setTimeout(() => void tick(), POLL_MS * 3);
      }
    };
    timer = window.setTimeout(() => void tick(), POLL_MS);
    return () => { stopped = true; window.clearTimeout(timer); };
  }, [activeId, request]);

  useEffect(() => {
    if (job && !isActive(job) && !reported.current.has(job.job_id)) {
      reported.current.add(job.job_id);
      finishedRef.current(job);
    }
  }, [job]);

  const start = useCallback(async (rows: PlanRow[]) => {
    const next = await request<AcceptJob>('/curate/accept', {
      method: 'POST',
      body: JSON.stringify({
        vault,
        items: rows.map((row) => ({
          id: row.id, verdict: row.verdict, advice_token: row.advice_token,
          ...(row.merge ? { merge: { target_node_id: row.merge.target_node_id, candidate_revision: row.merge.candidate_revision, target_revision: row.merge.target_revision } } : {}),
        })),
      }),
    });
    setJob(next);
    return next;
  }, [request, vault]);

  return { job, start, running: isActive(job), progress: isActive(job) ? { completed: job!.completed, total: job!.total } : null };
}

function Confidence({ value }: { value: number | null }) {
  const t = useT();
  if (value === null) return <span className="text-[11px] text-text-subtle">—</span>;
  const pct = Math.round(value * 100);
  const tone = value >= 0.8 ? 'bg-emerald-400' : value >= 0.6 ? 'bg-amber-400' : 'bg-rose-400';
  return <span className="flex items-center gap-1.5 text-[11px] tabular-nums text-text-muted" title={t('accept.confidenceTitle', { pct })}>
    <span className="h-1.5 w-10 overflow-hidden rounded-full bg-white/10"><span className={`block h-full ${tone}`} style={{ width: `${pct}%` }} /></span>{pct}%
  </span>;
}

function StateMark({ item }: { item?: AcceptItem }) {
  const t = useT();
  if (!item) return null;
  if (item.state === 'queued') return <span className="h-2 w-2 rounded-full bg-white/25" title={t('accept.queued')} />;
  if (item.state === 'running') return <Loader2 size={16} className="animate-spin text-sky-300" />;
  if (item.state === 'done') return <CheckCircle2 size={17} className="text-emerald-300" />;
  if (item.state === 'stale') return <AlertTriangle size={16} className="text-amber-300" />;
  return <XCircle size={16} className="text-rose-300" />;
}

function Row({ row, checked, onToggle, item, onOpen, onUndo, undoing }: {
  row: PlanRow; checked: boolean; onToggle: () => void; item?: AcceptItem;
  onOpen: () => void; onUndo?: () => void; undoing: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const verdict = VERDICT[row.verdict];
  const locked = !!item || !!row.blocked;
  const faded = item?.state === 'done';
  return <li className={`rounded-2xl border transition-colors ${checked && !item ? 'border-white/15 bg-white/[0.05]' : 'border-white/[0.07] bg-white/[0.02]'} ${faded ? 'opacity-70' : ''}`}>
    <div className="flex items-start gap-1">
      <button type="button" role="checkbox" aria-checked={checked} aria-label={t('accept.acceptAria', { title: row.title })} disabled={locked} onClick={onToggle}
        className="flex min-h-[52px] w-12 shrink-0 items-center justify-center rounded-l-2xl disabled:cursor-default">
        {item ? <StateMark item={item} /> : <span className={`flex h-[22px] w-[22px] items-center justify-center rounded-md border transition-colors ${row.blocked ? 'border-white/10 bg-white/[0.03]' : checked ? 'border-transparent bg-sky-400 text-slate-950' : 'border-white/25'}`}>{checked && <Check size={14} strokeWidth={3} />}</span>}
      </button>
      <div className="min-w-0 flex-1 py-3 pr-3">
        <button type="button" onClick={() => setOpen((v) => !v)} className="block w-full text-left" aria-expanded={open}>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${verdict.tone}`}><verdict.icon size={11} />{t(verdict.label)}</span>
            <Confidence value={row.confidence} />
            {row.source === 'scheduled' && <span className="rounded-md border border-white/10 px-1.5 text-[10px] uppercase tracking-[0.1em] text-text-subtle" title={t('accept.cronTitle')}>cron</span>}
            <ChevronDown size={14} className={`ml-auto shrink-0 text-text-subtle transition-transform ${open ? 'rotate-180' : ''}`} />
          </div>
          <p className="mt-1 text-[15px] font-medium leading-5 text-text sm:text-sm">{row.title}</p>
          {row.merge && <p className="mt-1 flex min-w-0 items-center gap-1 text-xs text-sky-200/90"><GitMerge size={12} className="shrink-0" /><span className="truncate">{row.merge.title || row.merge.note_path}</span></p>}
          <p className={`mt-1.5 text-[13px] leading-5 text-text-muted ${open ? '' : 'line-clamp-2'}`}>{row.reason || t('accept.noReason')}</p>
        </button>
        {row.blocked && <p className="mt-2 flex items-start gap-1.5 text-xs leading-5 text-amber-200/90"><AlertTriangle size={13} className="mt-0.5 shrink-0" />{row.blocked}</p>}
        {item?.error && <p className={`mt-2 text-xs leading-5 ${item.state === 'stale' ? 'text-amber-200/90' : 'text-rose-200/90'}`}>{item.error}</p>}
        {open && <div className="mt-3 space-y-2">
          {row.merge ? <>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[0.06] p-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-emerald-300/90">{t('accept.appendsTo', { title: row.merge.title })}</p>
              <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-emerald-50/90">{row.merge.claim}</p>
            </div>
            <div className="rounded-xl border border-white/[0.08] bg-black/15 p-2.5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-subtle">{t('accept.existingNote', { path: row.merge.note_path })}</p>
              <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-text-muted">{row.merge.target_excerpt}</p>
            </div>
          </> : <div className="rounded-xl border border-white/[0.08] bg-black/15 p-2.5">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-subtle">{t('accept.candidate')}</p>
            <p className="mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-text-muted">{row.definition}</p>
          </div>}
          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button type="button" onClick={onOpen} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-text-muted hover:bg-white/[0.06] hover:text-text"><ExternalLink size={13} /> {t('accept.decideManually')}</button>
            {row.model && <span className="text-[11px] text-text-subtle">{row.model}</span>}
          </div>
        </div>}
        {onUndo && <button type="button" onClick={onUndo} disabled={undoing} className="mt-2 inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-white/10 px-3 text-xs text-text-muted hover:bg-white/[0.06] hover:text-text disabled:opacity-50">
          {undoing ? <Loader2 size={13} className="animate-spin" /> : <Undo2 size={13} />} {t('accept.undo')}
        </button>}
      </div>
    </div>
  </li>;
}

export function AcceptReview({ vault, candidateIds, request, accept, onClose, onOpenCandidate, onChanged }: {
  vault: string;
  candidateIds: string[];
  request: Request;
  accept: ReturnType<typeof useAcceptJob>;
  onClose: () => void;
  onOpenCandidate: (id: string) => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [rows, setRows] = useState<PlanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<Verdict | 'all'>('all');
  const [starting, setStarting] = useState(false);
  const [undone, setUndone] = useState<Record<string, 'busy' | 'done' | string>>({});
  // Frozen at open: the route reloads candidates when the batch ends, the panel keeps its rows.
  const [idsKey] = useState(() => candidateIds.join(','));
  // A batch already running for this vault (reload, other tab) is shown, never re-planned.
  const [jobId, setJobId] = useState<string | null>(() => (accept.running ? accept.job?.job_id ?? null : null));
  const job = jobId && accept.job?.job_id === jobId ? accept.job : null;

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    request<{ rows: PlanRow[] }>('/curate/accept/plan', { method: 'POST', body: JSON.stringify({ vault, candidate_ids: idsKey ? idsKey.split(',') : [] }) })
      .then((data) => {
        if (cancelled) return;
        setRows(data.rows || []);
        setChecked(new Set((data.rows || []).filter((r) => r.preselected).map((r) => r.id)));
      })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : t('accept.loadFailed')); });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps -- a language switch must not re-plan
  }, [idsKey, request, vault]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow; };
  }, [onClose]);

  const all = rows || [];
  const byVerdict = useMemo(() => Object.fromEntries(VERDICTS.map((v) => [v.id, all.filter((r) => r.verdict === v.id)])) as Record<Verdict, PlanRow[]>, [all]);
  const shown = tab === 'all' ? all : byVerdict[tab];
  const selectable = (r: PlanRow) => !r.blocked;
  const picked = all.filter((r) => checked.has(r.id) && selectable(r));
  const pickedBy = (v: Verdict) => picked.filter((r) => r.verdict === v).length;
  const toggle = (id: string) => setChecked((cur) => { const next = new Set(cur); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const shownSelectable = shown.filter(selectable);
  const allShownPicked = shownSelectable.length > 0 && shownSelectable.every((r) => checked.has(r.id));
  const toggleShown = () => setChecked((cur) => {
    const next = new Set(cur);
    shownSelectable.forEach((r) => (allShownPicked ? next.delete(r.id) : next.add(r.id)));
    return next;
  });

  const apply = async () => {
    if (!picked.length) return;
    setStarting(true);
    setError(null);
    try {
      const started = await accept.start(picked);
      setJobId(started.job_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('accept.startFailed'));
    } finally {
      setStarting(false);
    }
  };

  const undo = async (row: PlanRow, operationId: string) => {
    setUndone((cur) => ({ ...cur, [row.id]: 'busy' }));
    try {
      await request('/synthesis/revert', { method: 'POST', body: JSON.stringify({ operation_id: operationId, vault }) });
      setUndone((cur) => ({ ...cur, [row.id]: 'done' }));
      onChanged();
    } catch (cause) {
      setUndone((cur) => ({ ...cur, [row.id]: cause instanceof Error ? cause.message : t('accept.undoFailed') }));
    }
  };

  const running = isActive(job);
  const finished = !!job && !running;
  const pct = job ? Math.round(((job.completed || 0) / Math.max(job.total, 1)) * 100) : 0;
  const summary = job?.summary;
  // Once a batch is running, the panel shows only its rows: the rest are not being applied.
  const visible = job ? shown.filter((r) => job.items[r.id]) : shown;
  const jobOnly = !!job && (!rows || visible.length === 0);

  return <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/70 sm:items-center sm:p-4" role="presentation"
    onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div role="dialog" aria-modal="true" aria-labelledby="accept-title"
      className="flex h-[100dvh] w-full flex-col overflow-hidden bg-surface sm:h-auto sm:max-h-[88dvh] sm:max-w-3xl sm:rounded-3xl sm:border sm:border-white/10 sm:shadow-2xl">
      <header className="shrink-0 border-b border-white/[0.08] px-4 pb-3 pt-[max(env(safe-area-inset-top),0.875rem)] sm:px-5 sm:pt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.16em] text-sky-300/80"><Sparkles size={12} /> {t('accept.eyebrow')}</p>
            <h2 id="accept-title" className="mt-1 text-lg font-semibold tracking-tight text-text">
              {running ? t('accept.applyingTitle', { done: job!.completed, total: job!.total }) : finished ? t('accept.doneTitle') : rows ? t('accept.toReview', { count: all.length }) : t('accept.loading')}
            </h2>
          </div>
          <button type="button" aria-label={t('common.close')} onClick={onClose} className="-mr-1 flex h-11 w-11 items-center justify-center rounded-xl text-text-muted hover:bg-white/[0.06] hover:text-text"><X size={20} /></button>
        </div>
        {job && <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10"><div className={`h-full transition-all duration-500 ${finished && (summary?.error || summary?.stale) ? 'bg-amber-400' : 'bg-emerald-400'}`} style={{ width: `${pct}%` }} /></div>}
        {rows && rows.length > 0 && <div className="-mx-4 mt-3 flex gap-1.5 overflow-x-auto px-4 pb-0.5 sm:mx-0 sm:px-0">
          {([{ id: 'all', label: 'accept.tabAll', dot: 'bg-white/40' }, ...VERDICTS] as { id: Verdict | 'all'; label: MessageKey; dot: string }[]).map((tabDef) => {
            const n = tabDef.id === 'all' ? all.length : byVerdict[tabDef.id].length;
            if (tabDef.id !== 'all' && !n) return null;
            const sel = tabDef.id === 'all' ? picked.length : pickedBy(tabDef.id);
            return <button key={tabDef.id} type="button" onClick={() => setTab(tabDef.id)}
              className={`flex min-h-9 shrink-0 items-center gap-1.5 rounded-full border px-3 text-[13px] transition-colors ${tab === tabDef.id ? 'border-white/25 bg-white/[0.09] text-text' : 'border-white/[0.08] text-text-muted hover:bg-white/[0.05]'}`}>
              <span className={`h-2 w-2 rounded-full ${tabDef.dot}`} />{t(tabDef.label)}<span className="tabular-nums text-text-subtle">{job ? n : `${sel}/${n}`}</span>
            </button>;
          })}
        </div>}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 py-3 sm:px-4">
        {error && <div className="mb-3 flex items-start gap-2 rounded-xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"><XCircle size={16} className="mt-0.5 shrink-0" />{error}</div>}
        {!rows && !error && !jobOnly && <div className="flex flex-col items-center gap-3 py-16 text-sm text-text-muted"><Loader2 size={22} className="animate-spin" />{t('accept.preparing')}</div>}
        {rows && !all.length && !job && <div className="py-16 text-center text-sm text-text-muted">{t('accept.empty')}<br />{t('accept.emptyHint')} <span className="text-text">{t('advisor.ask')}</span>.</div>}
        {rows && visible.length > 0 && !job && shownSelectable.length > 0 && <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-xs text-text-subtle">{t('accept.preselectedHint')}</span>
          <button type="button" onClick={toggleShown} className="min-h-9 shrink-0 rounded-lg px-2 text-xs font-medium text-sky-300 hover:bg-sky-400/10">{allShownPicked ? t('accept.none') : t('accept.tabAll')}</button>
        </div>}
        {jobOnly && <p className="py-6 text-center text-sm text-text-muted">{running ? t('accept.batchRunning') : t('accept.batchFinished')} ({job!.completed}/{job!.total}).</p>}
        <ul className="space-y-2">
          {visible.map((row) => {
            const item = job?.items[row.id];
            const opId = item?.state === 'done' && row.verdict !== 'reject' && !item.noop ? item.operation_id : undefined;
            const undoState = undone[row.id];
            return <Row key={row.id} row={row} checked={checked.has(row.id)} onToggle={() => toggle(row.id)} item={item}
              onOpen={() => onOpenCandidate(row.id)} undoing={undoState === 'busy'}
              onUndo={opId && undoState !== 'done' && finished ? () => void undo(row, opId) : undefined} />;
          })}
        </ul>
        {Object.entries(undone).filter(([, v]) => v !== 'busy' && v !== 'done').map(([id, msg]) => <p key={id} className="mt-2 text-xs text-rose-200/90">{t('accept.undoError', { id, error: msg })}</p>)}
      </div>

      <footer className="shrink-0 border-t border-white/[0.08] bg-surface px-4 pb-[max(env(safe-area-inset-bottom),0.875rem)] pt-3 sm:px-5">
        {job ? <div className="flex items-center justify-between gap-3">
          <p className="min-w-0 text-sm text-text-muted">
            {running ? t('accept.background') : job.status === 'failed' ? (job.error || t('accept.interrupted'))
              : <><span className="text-emerald-300">{t('accept.applied', { count: summary?.done ?? 0 })}</span>{!!summary?.stale && <> · <span className="text-amber-300">{t('accept.stale', { count: summary.stale })}</span></>}{!!summary?.error && <> · <span className="text-rose-300">{t('accept.errors', { count: summary.error })}</span></>}</>}
          </p>
          <button type="button" onClick={onClose} className="min-h-11 shrink-0 rounded-xl border border-white/10 px-5 text-sm font-medium text-text hover:bg-white/[0.06]">{running ? t('accept.hide') : t('common.close')}</button>
        </div> : <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap gap-1.5 text-xs">
            {VERDICTS.map((v) => pickedBy(v.id) > 0 && <span key={v.id} className={`rounded-full border px-2 py-0.5 ${v.tone}`}>{t(v.picked, { count: pickedBy(v.id) })}</span>)}
            {!picked.length && rows && all.length > 0 && <span className="text-text-subtle">{t('accept.tickHint')}</span>}
          </div>
          <button type="button" onClick={() => void apply()} disabled={!picked.length || starting || accept.running}
            className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-sky-400 px-5 text-[15px] font-semibold text-slate-950 transition-opacity hover:bg-sky-300 disabled:opacity-40 sm:w-auto sm:text-sm">
            {starting ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} strokeWidth={2.5} />}
            {accept.running ? t('accept.alreadyRunning') : picked.length ? t('accept.apply', { count: picked.length }) : t('accept.applyNone')}
          </button>
        </div>}
      </footer>
    </div>
  </div>;
}
