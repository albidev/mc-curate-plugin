import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Archive,
  Check,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  Inbox,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react';

interface Candidate {
  id: string;
  type?: string;
  title?: string;
  status: string;
  created?: string;
  body?: string;
  description?: string;
  confidence?: string | number;
  tags?: string | string[];
  sources?: string | string[];
  approved_at?: string;
  rejected_at?: string;
  rejection_reason?: string;
  quarantine_until?: string;
  promoted_at?: string;
  _filename?: string;
  sourceNotes?: Array<{ source: string; found: boolean; title: string; path?: string; body: string }>;
}

interface VaultInfo {
  id: string;
  label: string;
  mode: string;
  candidate_count: number;
  pending_count: number;
  reviewed_count: number;
  candidate_enabled: boolean;
  writable: boolean;
}

type StatusFilter = 'all' | 'pending' | 'approved' | 'applied' | 'rejected' | 'promoted';
type SortMode = 'newest' | 'oldest' | 'confidence';

const API_BASE = '/api/local';

async function requestJSON<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error || detail;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function statusLabel(status: string): string {
  return status.replaceAll('_', ' ');
}

function statusTone(status: string): string {
  if (status === 'pending' || status === 'pending_review') return 'border-amber-400/25 bg-amber-400/10 text-amber-300';
  if (status === 'approved' || status === 'promoted' || status === 'applied' || status === 'created' || status === 'merged') return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300';
  if (status === 'rejected') return 'border-rose-400/25 bg-rose-400/10 text-rose-300';
  return 'border-white/10 bg-white/[0.04] text-text-muted';
}

function confidenceValue(candidate: Candidate): number | null {
  const value = Number(candidate.confidence);
  return Number.isFinite(value) ? value : null;
}

function formatDate(value?: string): string {
  if (!value || value === 'null') return 'Unknown date';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

function parseList(value?: string | string[]): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  return value
    .replace(/^\[|\]$/g, '')
    .split(',')
    .map((item) => item.trim().replace(/^['"]|['"]$/g, ''))
    .filter(Boolean);
}

function Button({
  children,
  variant = 'secondary',
  disabled,
  onClick,
  type = 'button',
}: {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  disabled?: boolean;
  onClick?: () => void;
  type?: 'button' | 'submit';
}) {
  const styles = {
    primary: 'border-emerald-400/30 bg-emerald-400/15 text-emerald-200 hover:bg-emerald-400/25',
    secondary: 'border-white/10 bg-white/[0.05] text-text hover:bg-white/[0.09]',
    danger: 'border-rose-400/30 bg-rose-400/10 text-rose-200 hover:bg-rose-400/20',
    ghost: 'border-transparent bg-transparent text-text-muted hover:bg-white/[0.06] hover:text-text',
  }[variant];

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}

function CandidateCard({
  candidate,
  selected,
  onSelect,
}: {
  candidate: Candidate;
  selected: boolean;
  onSelect: () => void;
}) {
  const confidence = confidenceValue(candidate);
  const tags = parseList(candidate.tags);
  const isPending = candidate.status === 'pending' || candidate.status === 'pending_review';

  return (
    <button
      type="button"
      onClick={onSelect}
      className={`group w-full rounded-2xl border p-4 text-left transition-all ${
        selected
          ? 'border-sky-400/40 bg-sky-400/[0.08] shadow-[0_0_0_1px_rgba(56,189,248,0.08)]'
          : 'border-white/[0.08] bg-white/[0.025] hover:border-white/15 hover:bg-white/[0.05]'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${isPending ? 'bg-amber-400/10 text-amber-300' : 'bg-white/[0.06] text-text-muted'}`}>
          {isPending ? <Inbox size={17} /> : <Archive size={17} />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${statusTone(candidate.status)}`}>
              {statusLabel(candidate.status)}
            </span>
            {candidate.type && <span className="text-[11px] uppercase tracking-[0.12em] text-text-subtle">{candidate.type}</span>}
          </div>
          <h3 className="truncate text-[15px] font-semibold text-text">{candidate.title || candidate.id}</h3>
          <p className="mt-1 line-clamp-2 text-sm leading-5 text-text-muted">{candidate.body || 'No candidate summary available.'}</p>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-subtle">
            <span>{formatDate(candidate.created)}</span>
            {confidence !== null && <span>{Math.round(confidence * 100)}% confidence</span>}
            {tags.slice(0, 2).map((tag) => <span key={tag} className="text-sky-300/70">#{tag}</span>)}
          </div>
        </div>
        <ChevronRight size={17} className={`mt-1 shrink-0 transition-transform ${selected ? 'translate-x-0.5 text-sky-300' : 'text-text-subtle group-hover:translate-x-0.5 group-hover:text-text-muted'}`} />
      </div>
    </button>
  );
}

export function CurateRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [token, setToken] = useState('');
  const [vaults, setVaults] = useState<VaultInfo[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedVault, setSelectedVault] = useState(searchParams.get('vault') || 'core');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [sortMode, setSortMode] = useState<SortMode>('newest');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [rejecting, setRejecting] = useState<Candidate | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    setToken(localStorage.getItem('mission-control-token') || '');
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setRefreshing(true);
    setError(null);
    try {
      const [candidatePayload, vaultPayload] = await Promise.all([
        requestJSON<{ candidates: Candidate[] }>(`/candidates?vault=${encodeURIComponent(selectedVault)}`, token),
        requestJSON<{ vaults: VaultInfo[] }>('/candidates/vaults', token),
      ]);
      setCandidates(candidatePayload.candidates || []);
      setVaults(vaultPayload.vaults || []);
      // Details are opt-in: never open a candidate automatically on load.
      setSelectedId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Curate could not load its data.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedVault, token]);

  useEffect(() => {
    if (token) void load();
  }, [load, token]);

  useEffect(() => {
    if (!selectedId) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelectedId(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [selectedId]);

  const chooseVault = (vaultId: string) => {
    setSelectedVault(vaultId);
    setSearchParams((current) => {
      current.set('vault', vaultId);
      return current;
    });
  };

  const performAction = async (candidate: Candidate, action: 'approve' | 'reject', reason = '') => {
    setActionId(candidate.id);
    setError(null);
    try {
      await requestJSON(action === 'approve' ? '/candidates/approve' : '/candidates/reject', token, {
        method: 'POST',
        body: JSON.stringify({ id: candidate.id, vault: selectedVault, ...(reason ? { reason } : {}) }),
      });
      setNotice(action === 'approve' ? 'Candidate approved and moved to quarantine.' : 'Candidate rejected.');
      setRejecting(null);
      setRejectReason('');
      await load(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : `Could not ${action} candidate.`);
    } finally {
      setActionId(null);
    }
  };

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const visibleCandidates = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return candidates
      .filter((candidate: Candidate) => statusFilter === 'all' || candidate.status === statusFilter || (statusFilter === 'pending' && candidate.status === 'pending_review'))
      .filter((candidate: Candidate) => !normalizedQuery
        || [candidate.title, candidate.body, candidate.id].some((value) => typeof value === 'string' && value.toLowerCase().includes(normalizedQuery))
        || parseList(candidate.tags).some((value) => value.toLowerCase().includes(normalizedQuery)))
      .sort((a, b) => {
        if (sortMode === 'confidence') return (confidenceValue(b) || 0) - (confidenceValue(a) || 0);
        const left = new Date(a.created || 0).getTime();
        const right = new Date(b.created || 0).getTime();
        return sortMode === 'newest' ? right - left : left - right;
      });
  }, [candidates, query, sortMode, statusFilter]);

  const selectedCandidate = visibleCandidates.find((candidate) => candidate.id === selectedId)
    || candidates.find((candidate) => candidate.id === selectedId)
    || null;
  const pendingCount = candidates.filter((candidate) => candidate.status === 'pending' || candidate.status === 'pending_review').length;
  const approvedCount = candidates.filter((candidate: Candidate) => ['approved', 'promoted', 'applied', 'created', 'merged'].includes(candidate.status)).length;
  const averageConfidence = candidates.length
    ? candidates.reduce((sum, candidate) => sum + (confidenceValue(candidate) || 0), 0) / candidates.length
    : 0;
  const currentVault = vaults.find((vault) => vault.id === selectedVault);

  return (
    <div className="route-page-scroll min-h-full bg-transparent px-3 pb-8 pt-4 sm:px-5 lg:px-7">
      <div className="mx-auto flex w-full max-w-[1500px] flex-col gap-5">
        <header className="flex flex-col gap-4 border-b border-white/[0.08] pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-sky-300/80">
              <Sparkles size={13} /> Nightly brain / review queue
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-3xl">Curate</h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-muted">Review generated concepts before they become durable knowledge. Approvals enter quarantine first; nothing is silently promoted.</p>
          </div>
          <Button variant="secondary" onClick={() => void load(true)} disabled={refreshing}>
            <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} /> Refresh
          </Button>
        </header>

        {error && <div className="flex items-start gap-3 rounded-2xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"><XCircle size={17} className="mt-0.5 shrink-0" /><span>{error}</span></div>}
        {notice && <div className="flex items-center gap-3 rounded-2xl border border-emerald-400/25 bg-emerald-400/10 p-3 text-sm text-emerald-200"><CheckCircle2 size={17} className="shrink-0" /><span>{notice}</span></div>}

        <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {[
            { label: 'Pending review', value: pendingCount, icon: Inbox, tone: 'text-amber-300 bg-amber-400/10' },
            { label: 'Approved / applied', value: approvedCount, icon: CheckCircle2, tone: 'text-emerald-300 bg-emerald-400/10' },
            { label: 'In this vault', value: candidates.length, icon: Archive, tone: 'text-sky-300 bg-sky-400/10' },
            { label: 'Average confidence', value: candidates.length ? `${Math.round(averageConfidence * 100)}%` : '—', icon: ShieldCheck, tone: 'text-violet-300 bg-violet-400/10' },
          ].map(({ label, value, icon: Icon, tone }) => (
            <div key={label} className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
              <div className="flex items-center justify-between gap-3"><span className="text-xs text-text-muted">{label}</span><span className={`flex h-8 w-8 items-center justify-center rounded-xl ${tone}`}><Icon size={15} /></span></div>
              <div className="mt-3 text-2xl font-semibold tracking-tight text-text">{value}</div>
            </div>
          ))}
        </section>

        <section className="flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-3 sm:p-4">
          <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-semibold text-text">Vaults</p><p className="text-xs text-text-muted">Choose the knowledge space to review.</p></div><span className="text-xs text-text-subtle">{currentVault?.mode || 'loading'}</span></div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {vaults.map((vault) => (
              <button key={vault.id} type="button" onClick={() => chooseVault(vault.id)} className={`shrink-0 rounded-xl border px-3 py-2 text-left transition-colors ${selectedVault === vault.id ? 'border-sky-400/40 bg-sky-400/10 text-sky-200' : 'border-white/[0.08] bg-white/[0.025] text-text-muted hover:bg-white/[0.06]'}`}>
                <span className="block text-sm font-medium">{vault.label}</span><span className="mt-0.5 block text-[11px] text-current/70">{vault.candidate_count} total · {vault.pending_count} pending</span>
              </button>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-3 sm:flex-row sm:items-center sm:p-4">
          <label className="relative min-w-0 flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search title, content, tags, or ID…" className="h-10 w-full rounded-xl border border-white/[0.08] bg-black/10 pl-9 pr-3 text-sm text-text outline-none placeholder:text-text-subtle focus:border-sky-400/40" /></label>
          <div className="flex gap-2"><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)} className="h-10 min-w-32 rounded-xl border border-white/[0.08] bg-surface px-3 text-sm text-text outline-none"><option value="all">All status</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="applied">Applied</option><option value="rejected">Rejected</option><option value="promoted">Promoted</option></select><select value={sortMode} onChange={(event) => setSortMode(event.target.value as SortMode)} className="h-10 min-w-32 rounded-xl border border-white/[0.08] bg-surface px-3 text-sm text-text outline-none"><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="confidence">Confidence</option></select></div>
        </section>

        {loading ? (
          <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-text-muted"><Loader2 size={17} className="animate-spin" /> Loading candidate queue…</div>
        ) : (
          <section className="flex min-w-0 flex-col gap-3">
            <div className="flex items-center justify-between"><div><p className="text-sm font-semibold text-text">Candidate queue</p><p className="text-xs text-text-muted">{visibleCandidates.length} of {candidates.length} candidates visible</p></div><span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-xs text-text-muted">{statusFilter === 'all' ? 'All candidates' : statusLabel(statusFilter)}</span></div>
            {visibleCandidates.length ? visibleCandidates.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} selected={candidate.id === selectedId} onSelect={() => setSelectedId(candidate.id)} />) : <div className="rounded-2xl border border-dashed border-white/10 px-5 py-12 text-center"><ClipboardCheck size={28} className="mx-auto text-text-subtle" /><p className="mt-3 text-sm font-medium text-text">No candidates match</p><p className="mt-1 text-xs text-text-muted">Try another status, vault, or search term.</p></div>}
          </section>
        )}
      </div>

      {selectedCandidate && <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/65 p-0 sm:items-center sm:p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedId(null); }}>
        <div role="dialog" aria-modal="true" aria-labelledby="curate-candidate-title" className="max-h-[92vh] w-full overflow-hidden rounded-t-3xl border border-white/10 bg-surface shadow-2xl sm:max-w-3xl sm:rounded-3xl">
          <CandidateDetail candidate={selectedCandidate} actionId={actionId} onClose={() => setSelectedId(null)} onApprove={(candidate) => void performAction(candidate, 'approve')} onReject={(candidate) => { setRejecting(candidate); setRejectReason(''); }} />
        </div>
      </div>}

      {rejecting && <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-3 sm:items-center"><form onSubmit={(event) => { event.preventDefault(); void performAction(rejecting, 'reject', rejectReason.trim()); }} className="w-full max-w-lg rounded-2xl border border-white/10 bg-surface p-5 shadow-2xl"><div className="flex items-start justify-between gap-4"><div><p className="text-base font-semibold text-text">Reject candidate</p><p className="mt-1 text-sm text-text-muted">Give the nightly brain useful feedback for the next run.</p></div><button type="button" onClick={() => setRejecting(null)} className="rounded-lg p-1 text-text-muted hover:bg-white/[0.06] hover:text-text"><X size={18} /></button></div><textarea autoFocus value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} placeholder="Why should this candidate be rejected?" className="mt-4 min-h-28 w-full resize-y rounded-xl border border-white/10 bg-black/10 p-3 text-sm text-text outline-none placeholder:text-text-subtle focus:border-rose-400/40" /><div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={() => setRejecting(null)}>Cancel</Button><Button type="submit" variant="danger" disabled={!rejectReason.trim() || actionId === rejecting.id}>{actionId === rejecting.id && <Loader2 size={15} className="animate-spin" />} Reject candidate</Button></div></form></div>}
    </div>
  );
}

function CandidateDetail({
  candidate,
  actionId,
  onClose,
  onApprove,
  onReject,
}: {
  candidate: Candidate;
  actionId: string | null;
  onClose: () => void;
  onApprove: (candidate: Candidate) => void;
  onReject: (candidate: Candidate) => void;
}) {
  const confidence = confidenceValue(candidate);
  const tags = parseList(candidate.tags);
  const sources = parseList(candidate.sources);
  const isPending = candidate.status === 'pending' || candidate.status === 'pending_review';
  const fullNote = candidate.description?.trim() || '';

  return <div className="flex max-h-[92vh] min-h-0 flex-col">
    <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] p-5"><div className="min-w-0"><div className="flex items-center gap-2"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${statusTone(candidate.status)}`}>{statusLabel(candidate.status)}</span>{candidate.type && <span className="text-[11px] uppercase tracking-[0.12em] text-text-subtle">{candidate.type}</span>}</div><h2 id="curate-candidate-title" className="mt-3 truncate text-xl font-semibold tracking-tight text-text">{candidate.title || candidate.id}</h2><p className="mt-2 break-all font-mono text-[10px] text-text-subtle">{candidate.id}</p></div><button type="button" aria-label="Close candidate details" onClick={onClose} className="rounded-xl p-2 text-text-muted hover:bg-white/[0.06] hover:text-text"><X size={19} /></button></div>
    <div className="min-h-0 overflow-y-auto p-5"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Meta label="Created" value={formatDate(candidate.created)} /><Meta label="Confidence" value={confidence === null ? '—' : `${Math.round(confidence * 100)}%`} /><Meta label="Approved" value={formatDate(candidate.approved_at)} /><Meta label="Quarantine until" value={formatDate(candidate.quarantine_until)} /></div><div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">Description</p>{fullNote ? <article className="max-h-[32vh] overflow-y-auto whitespace-pre-wrap rounded-xl border border-white/[0.08] bg-black/10 p-3 text-sm leading-6 text-text-muted">{fullNote}</article> : <div className="rounded-xl border border-dashed border-amber-400/25 bg-amber-400/[0.06] p-3 text-sm leading-6 text-amber-100/80">This candidate contains metadata only. The semantic description is available in the source notes below.</div>}</div>{candidate.sourceNotes?.length ? <div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">Evidence from source notes</p><div className="space-y-3">{candidate.sourceNotes.map((note) => <section key={note.source} className="rounded-xl border border-white/[0.08] bg-black/10 p-3"><div className="flex items-center justify-between gap-3"><p className="text-sm font-medium text-text">{note.title}</p><span className={`text-[10px] uppercase tracking-[0.12em] ${note.found ? 'text-emerald-300' : 'text-text-subtle'}`}>{note.found ? 'found' : 'missing'}</span></div>{note.found && <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-text-muted">{note.body}</p>}</section>)}</div></div> : null}{tags.length > 0 && <DetailList label="Tags" items={tags} tone="sky" />}{sources.length > 0 && <DetailList label="Sources" items={sources} tone="neutral" />}{candidate.rejection_reason && <div className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-3"><p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-rose-300">Rejection feedback</p><p className="mt-2 text-sm leading-5 text-rose-100/80">{candidate.rejection_reason}</p></div>}</div>
    <div className="flex justify-end gap-2 border-t border-white/[0.08] p-4"><Button variant="ghost" onClick={onClose}>Close</Button>{isPending && <><Button variant="danger" onClick={() => onReject(candidate)} disabled={actionId === candidate.id}><XCircle size={15} /> Reject</Button><Button variant="primary" onClick={() => onApprove(candidate)} disabled={actionId === candidate.id}>{actionId === candidate.id ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} Approve candidate</Button></>}</div>
  </div>;
}

function Meta({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><p className="text-[10px] uppercase tracking-[0.12em] text-text-subtle">{label}</p><p className="mt-1 truncate text-xs text-text-muted">{value}</p></div>; }
function DetailList({ label, items, tone }: { label: string; items: string[]; tone: 'sky' | 'neutral' }) { return <div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">{label}</p><div className="flex flex-wrap gap-1.5">{items.map((item) => <span key={item} className={`rounded-lg border px-2 py-1 text-[11px] ${tone === 'sky' ? 'border-sky-400/20 bg-sky-400/10 text-sky-200' : 'border-white/10 bg-white/[0.04] text-text-muted'}`}>{item}</span>)}</div></div>; }
