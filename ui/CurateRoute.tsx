import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  Archive,
  Bot,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardCheck,
  Clock3,
  Inbox,
  GitMerge,
  Layers,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Undo2,
  X,
  XCircle,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkBreaks from 'remark-breaks';
import remarkGfm from 'remark-gfm';
import { approvalNotice } from './approval-notice';
import { paginateItems } from './pagination';
import { MergeDialog, type MergeResult } from './merge-dialog';
import { CuratorAdvicePanel, CuratorBadge, adviceToFields, useCuratorAdvisor, type AdviceItem } from './advisor';
import { AcceptReview, useAcceptJob, type AcceptJob } from './accept-review';
import { currentLocale, formatDate, tr, useT } from './i18n';
import { EN, type MessageKey } from './messages';

function InlineActionButton({
  variant,
  onClick,
  disabled,
  children,
  title,
}: {
  variant: 'primary' | 'danger' | 'ghost';
  onClick: (e: React.MouseEvent) => void;
  disabled?: boolean;
  children: React.ReactNode;
  title: string;
}) {
  const styles = {
    primary: 'border-emerald-400/30 bg-emerald-400/15 text-emerald-200 hover:bg-emerald-400/25',
    danger: 'border-rose-400/30 bg-rose-400/10 text-rose-200 hover:bg-rose-400/20',
    ghost: 'border-transparent bg-transparent text-text-muted hover:bg-white/[0.06] hover:text-text',
  }[variant];

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-xl border px-0 transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}
    >
      {children}
    </button>
  );
}

interface Candidate {
  id: string;
  vault_id?: string;
  source?: string;
  synthesis_id?: string;
  curator_merge_target?: string;
  curator_verdict?: string;
  curator_note?: string;
  curator_confidence?: string;
  curator_model?: string;
  curator_source?: string;
  curator_reviewed_at?: string;
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
  cluster_id?: string;
  cluster_members?: string;
  jev_choice?: string;
  jev_confidence?: string;
  jev_criteria_version?: string;
  auto_rejected_at?: string;
  auto_reject_reverted_at?: string;
  _filename?: string;
  sourceNotes?: Array<{ source: string; found: boolean; match_type?: 'found' | 'related' | 'missing'; title: string; path?: string; body: string }>;
  sourceNodeIds?: string[];
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
  error?: string | null;
}

type StatusFilter = 'all' | 'pending' | 'in_vault' | 'rejected' | 'auto_rejected' | 'pre_approved';
type SortMode = 'newest' | 'oldest' | 'confidence';

interface ClusterInfo {
  cluster_id: string;
  representative: string;
  representative_title: string;
  member_ids: string[];
  member_titles: string[];
  similarities: Record<string, number>;
  best_similarity: number | null;
  mean_similarity: number | null;
}

type CandidateQueueItem =
  | { kind: 'cluster'; cluster: ClusterInfo }
  | { kind: 'candidate'; candidate: Candidate };

const PAGE_SIZE = 25;

const API_BASE = '/api/local';
const CURATE_STATUS_ENDPOINT = '/curate/status';

function notifyCurateStatusChanged(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mc:plugin-status-changed', {
      detail: { endpoint: CURATE_STATUS_ENDPOINT },
    }));
  }
}

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
  const key = `status.${status}` as MessageKey;
  return key in EN ? tr(key) : status.replaceAll('_', ' ');
}

// Terminal states 'applied' (BDH synthesis flow) and 'promoted' (legacy
// vault-brain flow) mean the same thing: the note is in the vault.
// Two pipelines, one outcome — displayed as one.
const IN_VAULT_STATUSES = new Set(['applied', 'promoted', 'created', 'merged']);
function displayStatus(status: string): string {
  return IN_VAULT_STATUSES.has(status) ? 'in_vault' : status;
}

function statusTone(status: string): string {
  const s = displayStatus(status);
  if (s === 'pending' || s === 'pending_review' || s === 'pre_approved') return 'border-amber-400/25 bg-amber-400/10 text-amber-300';
  if (s === 'in_vault' || s === 'approved') return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300';
  if (s === 'rejected') return 'border-rose-400/25 bg-rose-400/10 text-rose-300';
  if (s === 'auto_rejected') return 'border-orange-400/25 bg-orange-400/10 text-orange-300';
  return 'border-white/10 bg-white/[0.04] text-text-muted';
}

function jevTone(confidence: number | null): string {
  if (confidence === null) return 'text-text-subtle';
  if (confidence >= 0.8) return 'text-emerald-300';
  if (confidence >= 0.6) return 'text-amber-300';
  return 'text-rose-300';
}

function confidenceValue(candidate: Candidate): number | null {
  const value = Number(candidate.confidence);
  if (Number.isFinite(value)) return value;
  // extractor confidence is often a word (low/medium/high), not a number
  const word = String(candidate.confidence || '').toLowerCase().trim();
  if (word === 'high') return 0.9;
  if (word === 'medium') return 0.66;
  if (word === 'low') return 0.33;
  return null;
}

function confidenceLabel(candidate: Candidate): string | null {
  // Prefer a word when the source was a word: "low" is honest, "33%" is fake precision.
  const raw = String(candidate.confidence || '').toLowerCase().trim();
  if (raw === 'high' || raw === 'medium' || raw === 'low') return tr(`confidence.${raw}`);
  const v = Number(candidate.confidence);
  if (Number.isFinite(v)) return `${Math.round(v * 100)}%`;
  return null;
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

function CandidateMarkdown({ content }: { content: string }) {
  return (
    <div className="chat-markdown min-w-0 text-sm leading-6 text-text-muted">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown>
    </div>
  );
}

function Button({
  children,
  variant = 'secondary',
  disabled,
  onClick,
  type = 'button',
  title,
  className = '',
}: {
  children: React.ReactNode;
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  disabled?: boolean;
  onClick?: () => void;
  type?: 'button' | 'submit';
  title?: string;
  className?: string;
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
      title={title}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

function PaginationControls({
  page,
  pageCount,
  start,
  end,
  total,
  itemLabel,
  onPageChange,
}: {
  page: number;
  pageCount: number;
  start: number;
  end: number;
  total: number;
  itemLabel: string;
  onPageChange: (page: number) => void;
}) {
  const t = useT();
  if (pageCount < 2) return null;

  return (
    <div className="flex flex-col gap-3 border-t border-white/[0.08] pt-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-xs text-text-muted" aria-live="polite">
        {t('pagination.showing', { start, end, total, items: itemLabel })}
      </p>
      <nav aria-label={t('pagination.aria')} className="flex items-center justify-between gap-2 sm:justify-end">
        <button
          type="button"
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1}
          aria-label={t('pagination.previousAria')}
          className="rounded-lg border border-white/10 px-3 py-2 text-xs font-medium text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t('pagination.previous')}
        </button>
        <span className="min-w-20 text-center text-xs text-text-muted">{t('pagination.page', { page, pages: pageCount })}</span>
        <button
          type="button"
          onClick={() => onPageChange(page + 1)}
          disabled={page >= pageCount}
          aria-label={t('pagination.nextAria')}
          className="rounded-lg border border-white/10 px-3 py-2 text-xs font-medium text-text-muted transition-colors hover:bg-white/[0.06] hover:text-text disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t('pagination.next')}
        </button>
      </nav>
    </div>
  );
}

function ClusterCard({
  cluster,
  candidates,
  expanded,
  onToggle,
  onApprove,
  onReject,
  onMerge,
  onSelectMember,
  actionId,
  live,
  onAsk,
}: {
  cluster: ClusterInfo;
  candidates: Candidate[];
  expanded: boolean;
  onToggle: () => void;
  onApprove: (candidate: Candidate) => void;
  onReject: (candidate: Candidate) => void;
  onSelectMember: (candidate: Candidate) => void;
  onMerge: (candidate: Candidate) => void;
  actionId: string | null;
  live?: AdviceItem;
  onAsk?: (candidate: Candidate) => void;
}) {
  const t = useT();
  const rep = candidates.find((c) => c.id === cluster.representative);
  const members = cluster.member_ids
    .map((id) => candidates.find((c) => c.id === id))
    .filter((c): c is Candidate => Boolean(c));
  const others = members.filter((c) => c.id !== cluster.representative);
  const isProcessing = actionId === cluster.representative;

  return (
    <div className="rounded-2xl border border-sky-400/20 bg-sky-400/[0.04] p-4 transition-colors hover:border-sky-400/30">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-sky-400/10 text-sky-300">
          <Layers size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-sky-400/25 bg-sky-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-sky-300">
              {t('cluster.badge', { count: cluster.member_ids.length })}
            </span>
            {cluster.mean_similarity !== null && (
              <span className="text-[11px] text-text-subtle">
                {t('cluster.similarity', { pct: (cluster.mean_similarity * 100).toFixed(0) })}
              </span>
            )}
            {rep && rep.jev_choice && (
              <span className={`text-[11px] ${jevTone(rep.jev_confidence ? Number(rep.jev_confidence) : null)}`}>
                jev: {rep.jev_choice}{rep.jev_confidence ? ` ${Math.round(Number(rep.jev_confidence) * 100)}%` : ''}
              </span>
            )}
            {rep && <CuratorBadge fields={rep} live={live} />}
          </div>
          <h3 className="truncate text-[15px] font-semibold text-text">{cluster.representative_title || cluster.representative}</h3>
          <p className="mt-1 line-clamp-2 text-sm leading-5 text-text-muted">{rep?.body || t('card.noSummary')}</p>
          {/* Details: collapsed by default */}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onToggle(); }}
            className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-sky-300/80 hover:text-sky-200"
            aria-expanded={expanded}
          >
            <ChevronDown size={13} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
            {expanded ? t('cluster.hide') : t('cluster.how', { count: others.length })}
          </button>
          {expanded && (
            <div className="mt-3 space-y-2 rounded-xl border border-white/[0.08] bg-black/10 p-3">
              <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-subtle">{t('cluster.members')}</p>
              {others.map((member) => (
                <div key={member.id} className="flex items-center justify-between gap-3 rounded-lg bg-white/[0.03] px-2.5 py-2">
                  <button type="button" onClick={() => onSelectMember(member)} className="min-w-0 flex-1 text-left">
                    <p className="truncate text-xs font-medium text-text">{member.title || member.id}</p>
                    <p className="truncate text-[11px] text-text-muted">{member.body?.slice(0, 110)}</p>
                  </button>
                  {cluster.similarities[member.id] !== undefined && (
                    <span className="shrink-0 rounded-md border border-white/10 px-1.5 py-0.5 text-[10px] text-text-muted">
                      {(cluster.similarities[member.id] * 100).toFixed(0)}%
                    </span>
                  )}
                </div>
              ))}
              {others.length === 0 && <p className="text-[11px] text-text-subtle">{t('cluster.single')}</p>}
              {rep?.jev_criteria_version && (
                <p className="text-[10px] text-text-subtle">{t('cluster.criteria', { version: rep.jev_criteria_version })}</p>
              )}
            </div>
          )}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {rep && onAsk && <InlineActionButton variant="ghost" title={t('cluster.askAi')} disabled={live?.state === 'queued' || live?.state === 'running'} onClick={() => onAsk(rep)}><Brain size={14}/></InlineActionButton>}
          {rep?.source === 'session_synthesis' && ['pending_review', 'pre_approved'].includes(rep.status) && <InlineActionButton variant="ghost" title={t('cluster.merge')} disabled={!!actionId} onClick={() => onMerge(rep)}><GitMerge size={14}/></InlineActionButton>}
          <InlineActionButton
            variant="primary"
            onClick={() => onApprove(rep || clusterToCandidate(cluster))}
            disabled={isProcessing}
            title={t('cluster.approve')}
          >
            <Check size={14} className={isProcessing ? 'animate-pulse' : ''} />
          </InlineActionButton>
          <InlineActionButton
            variant="danger"
            onClick={() => onReject(rep || clusterToCandidate(cluster))}
            disabled={isProcessing}
            title={t('cluster.reject')}
          >
            <XCircle size={14} />
          </InlineActionButton>
        </div>
      </div>
    </div>
  );
}

function clusterToCandidate(cluster: ClusterInfo): Candidate {
  return {
    id: cluster.representative,
    title: cluster.representative_title,
    status: 'pending_review',
    body: tr('cluster.body', { count: cluster.member_ids.length, titles: cluster.member_titles.join(' | ') }),
    cluster_id: cluster.cluster_id,
  } as Candidate;
}

function AutoRejectedCard({
  candidate,
  onRestore,
  onSelect,
  restoring,
}: {
  candidate: Candidate;
  onRestore: (candidate: Candidate) => void;
  onSelect: () => void;
  restoring: boolean;
}) {
  const t = useT();
  const conf = candidate.jev_confidence ? Number(candidate.jev_confidence) : confidenceValue(candidate);
  return (
    <div className="rounded-2xl border border-orange-400/20 bg-orange-400/[0.04] p-4">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-orange-400/10 text-orange-300">
          <Bot size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-orange-400/25 bg-orange-400/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-orange-300">
              {t('autoRejected.badge')}
            </span>
            {candidate.jev_choice && (
              <span className={`text-[11px] ${jevTone(conf)}`}>
                jev: {candidate.jev_choice}{conf !== null ? ` ${Math.round(conf * 100)}%` : ''}
              </span>
            )}
            <span className="text-[11px] text-text-subtle">{formatDate(candidate.auto_rejected_at || candidate.created)}</span>
          </div>
          <button type="button" onClick={onSelect} className="w-full text-left">
            <h3 className="truncate text-[15px] font-semibold text-text">{candidate.title || candidate.id}</h3>
            <p className="mt-1 line-clamp-2 text-sm leading-5 text-text-muted">{candidate.body || t('card.noSummary')}</p>
          </button>
        </div>
        <Button variant="secondary" disabled={restoring} onClick={() => onRestore(candidate)} title={t('autoRejected.restoreTitle')}>
          {restoring ? <Loader2 size={14} className="animate-spin" /> : <Undo2 size={14} />} {t('autoRejected.restore')}
        </Button>
      </div>
    </div>
  );
}

function CandidateCard({
  candidate,
  selected,
  onSelect,
  onApprove,
  onReject,
  onMerge,
  actionId,
  live,
  onAsk,
}: {
  candidate: Candidate;
  selected: boolean;
  onSelect: () => void;
  onApprove: (candidate: Candidate) => void;
  onReject: (candidate: Candidate) => void;
  onMerge: (candidate: Candidate) => void;
  actionId: string | null;
  live?: AdviceItem;
  onAsk?: (candidate: Candidate) => void;
}) {
  const t = useT();
  const confidence = confidenceValue(candidate);
  const tags = parseList(candidate.tags);
  const isPending = candidate.status === 'pending' || candidate.status === 'pending_review' || candidate.status === 'pre_approved';
  const isProcessing = actionId === candidate.id;

  return (
    <div
      onClick={onSelect}
      className={`group w-full rounded-2xl border p-4 text-left transition-all cursor-pointer ${
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
              {statusLabel(displayStatus(candidate.status))}
            </span>
            {candidate.type && <span className="text-[11px] uppercase tracking-[0.12em] text-text-subtle">{candidate.type}</span>}
          </div>
          <h3 className="truncate text-[15px] font-semibold text-text">{candidate.title || candidate.id}</h3>
          <p className="mt-1 line-clamp-2 text-sm leading-5 text-text-muted">{candidate.body || t('card.noSummary')}</p>
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-subtle">
            <span>{formatDate(candidate.created)}</span>
            {confidenceLabel(candidate) && (
              <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
                (() => {
                  const raw = String(candidate.confidence || '').toLowerCase().trim();
                  if (raw === 'high') return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-300';
                  if (raw === 'medium') return 'border-amber-400/25 bg-amber-400/10 text-amber-300';
                  if (raw === 'low') return 'border-rose-400/25 bg-rose-400/10 text-rose-300';
                  return 'border-white/10 bg-white/[0.04] text-text-muted';
                })()
              }`}>{t('card.extractor', { value: confidenceLabel(candidate) ?? '' })}</span>
            )}
            {candidate.jev_choice && (
              <span className={jevTone(candidate.jev_confidence ? Number(candidate.jev_confidence) : null)}>
                jev: {candidate.jev_choice}{candidate.jev_confidence ? ` ${Math.round(Number(candidate.jev_confidence) * 100)}%` : ''}
              </span>
            )}
            <CuratorBadge fields={candidate} live={live} />
            {tags.slice(0, 2).map((tag) => <span key={tag} className="text-sky-300/70">#{tag}</span>)}
          </div>
        </div>
        {isPending && (
          <div className="flex items-center gap-1.5 ml-auto shrink-0">
            {onAsk && <InlineActionButton variant="ghost" title={t('card.askAi')} disabled={live?.state === 'queued' || live?.state === 'running'} onClick={e => { e.stopPropagation(); onAsk(candidate); }}><Brain size={14}/></InlineActionButton>}
            {candidate.source === 'session_synthesis' && <InlineActionButton variant="ghost" title={t('card.merge')} disabled={!!actionId} onClick={e => { e.stopPropagation(); onMerge(candidate); }}><GitMerge size={14}/></InlineActionButton>}
            <InlineActionButton
              variant="primary"
              onClick={(e) => { e.stopPropagation(); onApprove(candidate); }}
              disabled={isProcessing}
              title={t('card.approve')}
            >
              <Check size={14} className={isProcessing ? 'animate-pulse' : ''} />
            </InlineActionButton>
            <InlineActionButton
              variant="danger"
              onClick={(e) => { e.stopPropagation(); onReject(candidate); }}
              disabled={isProcessing}
              title={t('card.reject')}
            >
              <XCircle size={14} />
            </InlineActionButton>
          </div>
        )}
        {!isPending && <ChevronRight size={17} className={`mt-1 shrink-0 transition-transform ${selected ? 'translate-x-0.5 text-sky-300' : 'text-text-subtle group-hover:translate-x-0.5 group-hover:text-text-muted'}`} />}
      </div>
    </div>
  );
}

export function CurateRoute() {
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const [token, setToken] = useState('');
  const [vaults, setVaults] = useState<VaultInfo[]>([]);
  const queueSectionRef = useRef<HTMLElement | null>(null);
  const autoRejectedSectionRef = useRef<HTMLElement | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [clusters, setClusters] = useState<ClusterInfo[]>([]);
  // The Jev gate lives in the optional Curate sidecar; its clustered endpoint reports when it is down.
  const [pipelineAvailable, setPipelineAvailable] = useState(false);
  // curate-vaults.yaml exists but could not be read: extra vaults and the advisor are off.
  const [configError, setConfigError] = useState<string | null>(null);
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [selectedVault, setSelectedVault] = useState(searchParams.get('vault') || '');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [sortMode, setSortMode] = useState<SortMode>('newest');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [rejecting, setRejecting] = useState<Candidate | null>(null);
  const [merging, setMerging] = useState<(Candidate & {vault_id: string}) | null>(null);
  const mergeRequest = useCallback(<T,>(path: string, init?: RequestInit) => requestJSON<T>(path, token, init), [token]);
  const [rejectReason, setRejectReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [classifying, setClassifying] = useState(false);
  const advisorEnabled = !!token && !!selectedVault && !!vaults.find((vault) => vault.id === selectedVault)?.writable;
  const applyAdvice = useCallback((candidateId: string, item: AdviceItem) => {
    // Same fields the worker persisted in extra.curator_*: the card flips now, a reload shows the same.
    setCandidates((current) => current.map((candidate) => candidate.id === candidateId
      ? { ...candidate, ...adviceToFields(item) }
      : candidate));
  }, []);
  const advisor = useCuratorAdvisor({ vault: selectedVault, request: mergeRequest, enabled: advisorEnabled, onAdvice: applyAdvice });
  const askAdvice = useCallback(async (targets: Candidate[]) => {
    const ids = targets.map((candidate) => candidate.id).filter(Boolean);
    if (!ids.length) return;
    setError(null);
    try {
      const job = await advisor.ask(ids);
      const skipped = Object.keys(job.skipped || {}).length;
      setNotice(tr('advice.started', { count: job.total, provider: job.provider, model: job.model })
        + (skipped ? tr('advice.skipped', { count: skipped }) : ''));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr('advice.startFailed'));
    }
  }, [advisor]);
  const canAskAdvice = useCallback((candidate: Candidate) => advisorEnabled
    && candidate.source === 'session_synthesis'
    && (candidate.status === 'pending_review' || candidate.status === 'pre_approved'), [advisorEnabled]);
  const [reviewing, setReviewing] = useState<string[] | null>(null);
  const [adviceReady, setAdviceReady] = useState(false);
  const acceptFinished = useCallback((job: AcceptJob) => {
    const s = job.summary;
    setNotice(job.status === 'failed'
      ? tr('accept.batchFailed', { error: job.error || tr('common.unknownError') })
      : tr('accept.appliedNotice', { done: s?.done ?? 0 })
        + (s?.stale ? tr('accept.staleNotice', { count: s.stale }) : '')
        + (s?.error ? tr('accept.errorNotice', { count: s.error }) : ''));
    notifyCurateStatusChanged();
    void loadRef.current(true);
  }, []);
  const accept = useAcceptJob({ vault: selectedVault, request: mergeRequest, enabled: advisorEnabled, onFinished: acceptFinished });
  // When a bulk Ask AI finishes, offer the review instead of leaving 80 badges to click through.
  const advisorWasBusy = useRef(false);
  useEffect(() => {
    if (advisor.progress) advisorWasBusy.current = true;
    else if (advisorWasBusy.current) { advisorWasBusy.current = false; setAdviceReady(true); }
  }, [advisor.progress]);

  useEffect(() => {
    setToken(localStorage.getItem('mission-control-token') || '');
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setRefreshing(true);
    setError(null);
    try {
      const candidatePath = selectedVault
        ? `/candidates?vault=${encodeURIComponent(selectedVault)}`
        : '/candidates';
      const [candidateResult, vaultResult, clusterResult] = await Promise.allSettled([
        requestJSON<{ candidates: Candidate[]; vault?: string | null }>(candidatePath, token),
        requestJSON<{ vaults: VaultInfo[]; default_vault?: string | null; config_error?: string | null }>('/candidates/vaults', token),
        requestJSON<{ clusters: ClusterInfo[]; sidecar?: string }>(selectedVault ? `/candidates/clustered?vault=${encodeURIComponent(selectedVault)}` : '/candidates/clustered', token),
      ]);
      if (vaultResult.status === 'rejected') throw vaultResult.reason;
      const vaultPayload = vaultResult.value;
      setConfigError(vaultPayload.config_error || null);
      setVaults(vaultPayload.vaults || []);
      if (clusterResult.status === 'fulfilled') {
        setClusters(clusterResult.value.clusters || []);
        setPipelineAvailable(clusterResult.value.sidecar !== 'unavailable');
      } else {
        // clustering unavailable (older backend / pipeline down): flat list
        setClusters([]);
        setPipelineAvailable(false);
      }
      if (candidateResult.status === 'rejected') {
        setCandidates([]);
        throw candidateResult.reason;
      }
      const candidatePayload = candidateResult.value;
      const nextCandidates = candidatePayload.candidates || [];
      const nextVault = selectedVault
        || candidatePayload.vault
        || vaultPayload.default_vault
        || nextCandidates.find((candidate) => candidate.vault_id)?.vault_id
        || '';
      setCandidates(nextCandidates);
      if (!selectedVault && nextVault) setSelectedVault(nextVault);
      notifyCurateStatusChanged();
      // Details are opt-in: never open a candidate automatically on load.
      setSelectedId(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr('load.failed'));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [selectedVault, token]);

  const loadRef = useRef(load);
  loadRef.current = load;

  const runClassify = useCallback(async () => {
    setClassifying(true);
    setError(null);
    try {
      const result = await requestJSON<{ classified?: number; evaluated?: number; total?: number; skipped?: number; errors?: number }>(
        '/candidates/classify', token, { method: 'POST' });
      const n = result.classified ?? result.evaluated ?? result.total ?? 0;
      setNotice(result.errors
        ? tr('gate.doneWithErrors', { count: n, errors: result.errors })
        : tr('gate.done', { count: n }));
      await load(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr('gate.unavailable'));
    } finally {
      setClassifying(false);
    }
  }, [load, token]);

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
    setMerging(null);
    setSelectedId(null);
    setSelectedVault(vaultId);
    setCurrentPage(1);
    setSearchParams((current) => {
      current.set('vault', vaultId);
      return current;
    });
  };

  const changePage = (page: number) => {
    setCurrentPage(page);
    const section = statusFilter === 'auto_rejected' ? autoRejectedSectionRef.current : queueSectionRef.current;
    section?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const openMerge = (candidate: Candidate) => {
    if (actionId || restoringId || classifying) return;
    const vault = candidate.vault_id || selectedVault;
    if (!vault || candidate.source !== 'session_synthesis' || !['pending_review', 'pre_approved'].includes(candidate.status)) return;
    if (vaults.find(v => v.id === vault)?.writable === false) return;
    setSelectedId(null);
    setMerging({...candidate, vault_id: vault});
  };

  const mergedSuccessfully = (result: MergeResult) => {
    setNotice(result.status === 'noop' ? tr('merge.noopNotice', { path: result.note_path }) : tr('merge.doneNotice', { path: result.note_path }));
    setMerging(null);
    notifyCurateStatusChanged();
    void load(true);
  };

  const performAction = async (candidate: Candidate, action: 'approve' | 'reject', reason = '') => {
    if (actionId || merging) return;
    if (action === 'approve' && candidate.source === 'session_synthesis' && candidate.curator_merge_target) { openMerge(candidate); return; }
    setActionId(candidate.id);
    setError(null);
    const vault = selectedVault || candidate.vault_id || '';
    try {
      const result = await requestJSON<{ success: boolean; candidate?: Candidate }>(
        action === 'approve' ? '/candidates/approve' : '/candidates/reject', token, {
          method: 'POST',
          body: JSON.stringify({ id: candidate.id, vault, ...(reason ? { reason } : {}) }),
        },
      );
      setNotice(action === 'approve' ? approvalNotice(result.candidate ?? candidate, currentLocale()) : tr('reject.doneNotice'));
      setRejecting(null);
      setRejectReason('');
      await load(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr(action === 'approve' ? 'action.approveFailed' : 'action.rejectFailed'));
    } finally {
      setActionId(null);
    }
  };

  const performRestore = async (candidate: Candidate) => {
    setRestoringId(candidate.id);
    setError(null);
    const vault = selectedVault || candidate.vault_id || '';
    try {
      await requestJSON('/candidates/restore', token, {
        method: 'POST',
        body: JSON.stringify({ id: candidate.id, vault }),
      });
      setNotice(tr('restore.doneNotice'));
      await load(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : tr('restore.failed'));
    } finally {
      setRestoringId(null);
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
      .filter((candidate: Candidate) => statusFilter === 'all'
        || candidate.status === statusFilter
        || (statusFilter === 'in_vault' && IN_VAULT_STATUSES.has(candidate.status))
        || (statusFilter === 'pending' && candidate.status === 'pending_review'))
      .filter((candidate: Candidate) => !normalizedQuery
        || [candidate.title, candidate.body, candidate.id].some((value) => typeof value === 'string' && value.toLowerCase().includes(normalizedQuery))
        || parseList(candidate.tags).some((value) => value.toLowerCase().includes(normalizedQuery)))
      .sort((a, b) => {
        // pre-approved always first: it's the one-click confirm queue
        const rank = (c: Candidate) => (c.status === 'pre_approved' ? 0 : 1);
        const preDiff = rank(a) - rank(b);
        if (preDiff !== 0) return preDiff;
        if (sortMode === 'confidence') return (confidenceValue(b) || 0) - (confidenceValue(a) || 0);
        const left = new Date(a.created || 0).getTime();
        const right = new Date(b.created || 0).getTime();
        return sortMode === 'newest' ? right - left : left - right;
      });
  }, [candidates, query, sortMode, statusFilter]);

  const selectedCandidate = visibleCandidates.find((candidate) => candidate.id === selectedId)
    || candidates.find((candidate) => candidate.id === selectedId)
    || null;
  // Multi-member clusters only: singletons render as normal cards
  const multiClusters = useMemo(
    () => clusters.filter((cluster) => cluster.member_ids.length > 1),
    [clusters],
  );
  const queueItems = useMemo<CandidateQueueItem[]>(() => {
    const visibleById = new Map(visibleCandidates.map((candidate) => [candidate.id, candidate]));
    const clusterByMember = new Map<string, ClusterInfo>();
    for (const cluster of multiClusters) {
      for (const memberId of cluster.member_ids) {
        if (!clusterByMember.has(memberId)) clusterByMember.set(memberId, cluster);
      }
    }

    const items: CandidateQueueItem[] = [];
    const consumedIds = new Set<string>();
    for (const candidate of visibleCandidates) {
      if (consumedIds.has(candidate.id)) continue;
      const cluster = clusterByMember.get(candidate.id);
      if (cluster) {
        const memberIds = [...new Set(cluster.member_ids.filter((id) => visibleById.has(id)))];
        const members = memberIds
          .map((id) => visibleById.get(id))
          .filter((member): member is Candidate => Boolean(member));
        if (members.length > 1) {
          const representative = members.find((member) => member.id === cluster.representative) || members[0];
          items.push({
            kind: 'cluster',
            cluster: {
              ...cluster,
              representative: representative.id,
              representative_title: representative.title || representative.id,
              member_ids: members.map((member) => member.id),
              member_titles: members.map((member) => member.title || member.id),
            },
          });
          for (const member of members) consumedIds.add(member.id);
          continue;
        }
      }
      items.push({ kind: 'candidate', candidate });
      consumedIds.add(candidate.id);
    }
    return items;
  }, [multiClusters, visibleCandidates]);
  const queuePage = useMemo(
    () => paginateItems(queueItems, currentPage, PAGE_SIZE),
    [currentPage, queueItems],
  );
  const autoRejected = candidates.filter((candidate) => candidate.status === 'auto_rejected');
  const autoRejectedList = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return autoRejected.filter((candidate: Candidate) => !normalizedQuery
      || [candidate.title, candidate.body, candidate.id].some((value) => typeof value === 'string' && value.toLowerCase().includes(normalizedQuery)));
  }, [autoRejected, query]);
  const autoRejectedPage = useMemo(
    () => paginateItems(autoRejectedList, currentPage, PAGE_SIZE),
    [autoRejectedList, currentPage],
  );
  useEffect(() => {
    const clampedPage = statusFilter === 'auto_rejected' ? autoRejectedPage.page : queuePage.page;
    if (currentPage !== clampedPage) setCurrentPage(clampedPage);
  }, [autoRejectedPage.page, currentPage, queuePage.page, statusFilter]);
  // Bulk Ask AI only targets what the advisor has not answered yet (nor is answering now);
  // a cron opinion is an older model's and stays askable. Re-asking one card: its detail panel.
  const askableBulk = visibleCandidates.filter((candidate) => canAskAdvice(candidate)
    && candidate.curator_source !== 'on_demand' && !advisor.isBusy(candidate.id));
  const reviewableIds = visibleCandidates.filter((candidate) => canAskAdvice(candidate) && !!candidate.curator_verdict).map((candidate) => candidate.id);
  const pendingCount = candidates.filter((candidate) => candidate.status === 'pending' || candidate.status === 'pending_review' || candidate.status === 'pre_approved').length;
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
              <Brain size={13} /> {t('page.eyebrow')}
            </div>
            <h1 className="text-2xl font-semibold tracking-tight text-text sm:text-3xl">{t('page.title')}</h1>
            <p className="mt-1 max-w-2xl text-sm leading-6 text-text-muted">{t('page.subtitle')}</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {advisor.advisor?.configured && <Button variant="secondary" onClick={() => void askAdvice(askableBulk)}
              disabled={!!advisor.progress || !askableBulk.length}
              title={askableBulk.length ? t('askAi.title', { count: askableBulk.length, provider: advisor.advisor.provider ?? '', model: advisor.advisor.model ?? '' }) : t('askAi.allAnswered')} className="px-3 text-sm">
              {advisor.progress ? <Loader2 size={15} className="animate-spin" /> : <Brain size={15} />}
              <span className="hidden sm:inline">{advisor.progress ? t('askAi.progress', { done: advisor.progress.completed, total: advisor.progress.total }) : t('askAi.label', { count: askableBulk.length })}</span>
              <span className="sm:hidden">{advisor.progress ? `${advisor.progress.completed}/${advisor.progress.total}` : t('askAi.short', { count: askableBulk.length })}</span>
            </Button>}
            {(reviewableIds.length > 0 || accept.running) && <Button variant={adviceReady ? 'primary' : 'secondary'} onClick={() => { setAdviceReady(false); setReviewing(reviewableIds); }}
              disabled={!!advisor.progress} title={t('review.title')} className="px-3 text-sm">
              {accept.running ? <Loader2 size={15} className="animate-spin" /> : <ClipboardCheck size={15} />}
              <span className="hidden sm:inline">{accept.progress ? t('review.applying', { done: accept.progress.completed, total: accept.progress.total }) : t('review.label', { count: reviewableIds.length })}</span>
              <span className="sm:hidden">{accept.progress ? `${accept.progress.completed}/${accept.progress.total}` : t('review.short', { count: reviewableIds.length })}</span>
            </Button>}
            {pipelineAvailable && <Button variant="secondary" onClick={() => void runClassify()} disabled={classifying || refreshing} title={t('gate.title')} className="px-3 text-sm">
              <Sparkles size={15} className={classifying ? 'animate-spin' : ''} /> <span className="hidden sm:inline">{classifying ? t('gate.running') : t('gate.run')}</span><span className="sm:hidden">{t('gate.short')}</span>
            </Button>}
            <Button variant="secondary" onClick={() => void load(true)} disabled={refreshing}>
              <RefreshCw size={15} className={refreshing ? 'animate-spin' : ''} /> {t('common.refresh')}
            </Button>
          </div>
        </header>

        {adviceReady && reviewableIds.length > 0 && !reviewing && <div className="flex flex-col gap-3 rounded-2xl border border-sky-400/25 bg-sky-400/10 p-3 text-sm text-sky-100 sm:flex-row sm:items-center sm:justify-between">
          <span className="flex items-start gap-2"><Brain size={17} className="mt-0.5 shrink-0" />{t('ready.text', { count: reviewableIds.length })}</span>
          <div className="flex shrink-0 gap-2"><Button variant="ghost" onClick={() => setAdviceReady(false)} className="px-3 text-sm">{t('ready.later')}</Button><Button variant="primary" onClick={() => { setAdviceReady(false); setReviewing(reviewableIds); }} className="px-4 text-sm"><ClipboardCheck size={15} /> {t('ready.review')}</Button></div>
        </div>}
        {advisor.failures.length > 0 && <div className="flex items-start gap-3 rounded-2xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"><XCircle size={17} className="mt-0.5 shrink-0" /><span>{t('advice.failed', { error: advisor.failures[advisor.failures.length - 1] })}</span></div>}
        {error && <div className="flex items-start gap-3 rounded-2xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"><XCircle size={17} className="mt-0.5 shrink-0" /><span>{error}</span></div>}
        {configError && <div className="flex items-start gap-3 rounded-2xl border border-amber-400/25 bg-amber-400/10 p-3 text-sm text-amber-100"><AlertTriangle size={17} className="mt-0.5 shrink-0" /><span>{t('config.unreadable', { detail: configError })}</span></div>}
        {notice && <div className="flex items-center gap-3 rounded-2xl border border-emerald-400/25 bg-emerald-400/10 p-3 text-sm text-emerald-200"><CheckCircle2 size={17} className="shrink-0" /><span>{notice}</span></div>}

        <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {[
            { label: t('stats.pending'), value: pendingCount, icon: Inbox, tone: 'text-amber-300 bg-amber-400/10' },
            { label: t('stats.approved'), value: approvedCount, icon: CheckCircle2, tone: 'text-emerald-300 bg-emerald-400/10' },
            { label: t('stats.inVault'), value: candidates.length, icon: Archive, tone: 'text-sky-300 bg-sky-400/10' },
            { label: t('stats.avgConfidence'), value: candidates.length ? `${Math.round(averageConfidence * 100)}%` : '—', icon: ShieldCheck, tone: 'text-violet-300 bg-violet-400/10' },
          ].map(({ label, value, icon: Icon, tone }) => (
            <div key={label} className="rounded-2xl border border-white/[0.08] bg-white/[0.025] p-4">
              <div className="flex items-center justify-between gap-3"><span className="text-xs text-text-muted">{label}</span><span className={`flex h-8 w-8 items-center justify-center rounded-xl ${tone}`}><Icon size={15} /></span></div>
              <div className="mt-3 text-2xl font-semibold tracking-tight text-text">{value}</div>
            </div>
          ))}
        </section>

        <section className="flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-3 sm:p-4">
          <div className="flex items-center justify-between gap-3"><div><p className="text-sm font-semibold text-text">{t('vaults.title')}</p><p className="text-xs text-text-muted">{t('vaults.subtitle')}</p></div><span className="text-xs text-text-subtle">{currentVault?.mode || t('common.loading')}</span></div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {vaults
              .filter((vault: VaultInfo) => vault.candidate_enabled !== false && !vault.error && vault.mode !== 'error')
              .map((vault) => (
              <button key={vault.id} type="button" onClick={() => chooseVault(vault.id)} className={`shrink-0 rounded-xl border px-3 py-2 text-left transition-colors ${selectedVault === vault.id ? 'border-sky-400/40 bg-sky-400/10 text-sky-200' : 'border-white/[0.08] bg-white/[0.025] text-text-muted hover:bg-white/[0.06]'}`}>
                <span className="block text-sm font-medium">{vault.label}</span><span className="mt-0.5 block text-[11px] text-current/70">{t('vaults.counts', { total: vault.candidate_count, pending: vault.pending_count })}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.025] p-3 sm:flex-row sm:items-center sm:p-4">
          <label className="relative min-w-0 flex-1">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle" />
            <input
              value={query}
              onChange={(event) => { setQuery(event.target.value); setCurrentPage(1); }}
              placeholder={t('search.placeholder')}
              className="h-10 w-full rounded-xl border border-white/[0.08] bg-black/10 pl-9 pr-3 text-sm text-text outline-none placeholder:text-text-subtle focus:border-sky-400/40"
            />
          </label>
          <div className="flex gap-2">
            <select
              value={statusFilter}
              onChange={(event) => { setStatusFilter(event.target.value as StatusFilter); setCurrentPage(1); }}
              className="h-10 min-w-32 rounded-xl border border-white/[0.08] bg-surface px-3 text-sm text-text outline-none"
            >
              <option value="all">{t('filter.all')}</option>
              <option value="pending">{t('filter.pending')}</option>
              <option value="pre_approved">{t('filter.preApproved')}</option>
              <option value="auto_rejected">{t('filter.autoRejected')}</option>
              <option value="in_vault">{t('filter.inVault')}</option>
              <option value="rejected">{t('filter.rejected')}</option>
            </select>
            <select
              value={sortMode}
              onChange={(event) => { setSortMode(event.target.value as SortMode); setCurrentPage(1); }}
              className="h-10 min-w-32 rounded-xl border border-white/[0.08] bg-surface px-3 text-sm text-text outline-none"
            >
              <option value="newest">{t('sort.newest')}</option>
              <option value="oldest">{t('sort.oldest')}</option>
              <option value="confidence">{t('sort.confidence')}</option>
            </select>
          </div>
        </section>

        {loading ? (
          <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-text-muted"><Loader2 size={17} className="animate-spin" /> {t('queue.loading')}</div>
        ) : (
          <>
          {statusFilter === 'auto_rejected' ? (
            /* Auto-rejected audit section with revert */
            <section ref={autoRejectedSectionRef} className="flex min-w-0 flex-col gap-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-text">{t('autoRejected.title')}</p>
                  <p className="text-xs text-text-muted">{t('autoRejected.subtitle')}</p>
                </div>
                <span className="rounded-full bg-orange-400/10 px-2.5 py-1 text-xs text-orange-300">
                  {t('autoRejected.counts', { matching: autoRejectedList.length, total: autoRejected.length })}
                </span>
              </div>
              {autoRejectedPage.items.length ? autoRejectedPage.items.map((candidate) => (
                <AutoRejectedCard
                  key={`${candidate.id}:${candidate._filename ?? ''}`}
                  candidate={candidate}
                  onRestore={(c) => void performRestore(c)}
                  onSelect={() => setSelectedId(candidate.id)}
                  restoring={restoringId === candidate.id}
                />
              )) : (
                <div className="rounded-2xl border border-dashed border-orange-400/20 px-5 py-10 text-center">
                  <Bot size={28} className="mx-auto text-text-subtle" />
                  <p className="mt-3 text-sm font-medium text-text">
                    {autoRejected.length ? t('autoRejected.noMatch') : t('autoRejected.none')}
                  </p>
                  <p className="mt-1 text-xs text-text-muted">
                    {autoRejected.length ? t('autoRejected.tryAnother') : t('autoRejected.noneYet')}
                  </p>
                </div>
              )}
              <PaginationControls
                page={autoRejectedPage.page}
                pageCount={autoRejectedPage.pageCount}
                start={autoRejectedPage.start}
                end={autoRejectedPage.end}
                total={autoRejectedPage.total}
                itemLabel={t('common.candidates')}
                onPageChange={changePage}
              />
            </section>
          ) : (
          <section ref={queueSectionRef} className="flex min-w-0 flex-col gap-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-semibold text-text">{t('queue.title')}</p>
                <p className="text-xs text-text-muted">{t('queue.counts', { matching: visibleCandidates.length, cards: queueItems.length })}</p>
              </div>
              <span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-xs text-text-muted">
                {statusFilter === 'all' ? t('queue.all') : statusLabel(statusFilter)}
              </span>
            </div>
            {queuePage.items.map((item) => item.kind === 'cluster' ? (
              <ClusterCard
                key={item.cluster.cluster_id}
                cluster={item.cluster}
                candidates={candidates}
                expanded={expandedClusters.has(item.cluster.cluster_id)}
                onToggle={() => setExpandedClusters((current) => {
                  const next = new Set(current);
                  if (next.has(item.cluster.cluster_id)) next.delete(item.cluster.cluster_id); else next.add(item.cluster.cluster_id);
                  return next;
                })}
                onApprove={(candidate) => void performAction(candidate, 'approve')}
                onMerge={openMerge}
                onReject={(candidate) => { setRejecting(candidate); setRejectReason(''); }}
                onSelectMember={(member) => setSelectedId(member.id)}
                actionId={actionId}
                live={advisor.liveById.get(item.cluster.representative)}
                onAsk={candidates.some((c) => c.id === item.cluster.representative && canAskAdvice(c)) ? (candidate) => void askAdvice([candidate]) : undefined}
              />
            ) : (
              <CandidateCard
                key={`${item.candidate.id}:${item.candidate._filename ?? ''}`}
                candidate={item.candidate}
                selected={item.candidate.id === selectedId}
                onSelect={() => setSelectedId(item.candidate.id)}
                onApprove={(candidate) => void performAction(candidate, 'approve')}
                onMerge={openMerge}
                onReject={(candidate) => { setRejecting(candidate); setRejectReason(''); }}
                actionId={actionId}
                live={advisor.liveById.get(item.candidate.id)}
                onAsk={canAskAdvice(item.candidate) ? (candidate) => void askAdvice([candidate]) : undefined}
              />
            ))}
            {!queueItems.length && <div className="rounded-2xl border border-dashed border-white/10 px-5 py-12 text-center"><ClipboardCheck size={28} className="mx-auto text-text-subtle" /><p className="mt-3 text-sm font-medium text-text">{t('queue.noMatch')}</p><p className="mt-1 text-xs text-text-muted">{t('queue.tryAnother')}</p></div>}
            <PaginationControls
              page={queuePage.page}
              pageCount={queuePage.pageCount}
              start={queuePage.start}
              end={queuePage.end}
              total={queuePage.total}
              itemLabel={t('common.cards')}
              onPageChange={changePage}
            />
          </section>
          )}
          </>
        )}
      </div>

      {selectedCandidate && <div className="fixed inset-0 z-40 flex items-end justify-center bg-black/65 p-0 sm:items-center sm:p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSelectedId(null); }}>
        <div role="dialog" aria-modal="true" aria-labelledby="curate-candidate-title" className="max-h-[92vh] w-full overflow-hidden rounded-t-3xl border border-white/10 bg-surface shadow-2xl sm:max-w-3xl sm:rounded-3xl">
          <CandidateDetail candidate={selectedCandidate} actionId={actionId} onMerge={openMerge}
            live={advisor.liveById.get(selectedCandidate.id)}
            canAsk={canAskAdvice(selectedCandidate)} onAsk={(candidate) => void askAdvice([candidate])} onClose={() => setSelectedId(null)} onApprove={(candidate) => void performAction(candidate, 'approve')} onReject={(candidate) => { setRejecting(candidate); setRejectReason(''); }} />
        </div>
      </div>}

      {reviewing && <AcceptReview vault={selectedVault} candidateIds={reviewing} request={mergeRequest} accept={accept}
        onClose={() => setReviewing(null)} onOpenCandidate={(id) => { setReviewing(null); setSelectedId(id); }} onChanged={() => void load(true)} />}

      {merging && <MergeDialog key={`${merging.vault_id}:${merging.id}`} candidate={merging} request={mergeRequest} onCancel={() => setMerging(null)} onSuccess={mergedSuccessfully}/>}

      {rejecting && <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-3 sm:items-center"><form onSubmit={(event) => { event.preventDefault(); void performAction(rejecting, 'reject', rejectReason.trim()); }} className="w-full max-w-lg rounded-2xl border border-white/10 bg-surface p-5 shadow-2xl"><div className="flex items-start justify-between gap-4"><div><p className="text-base font-semibold text-text">{t('reject.title')}</p><p className="mt-1 text-sm text-text-muted">{t('reject.subtitle')}</p></div><button type="button" onClick={() => setRejecting(null)} className="rounded-lg p-1 text-text-muted hover:bg-white/[0.06] hover:text-text"><X size={18} /></button></div><textarea autoFocus value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} placeholder={t('reject.placeholder')} className="mt-4 min-h-28 w-full resize-y rounded-xl border border-white/10 bg-black/10 p-3 text-sm text-text outline-none placeholder:text-text-subtle focus:border-rose-400/40" /><div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={() => setRejecting(null)}>{t('common.cancel')}</Button><Button type="submit" variant="danger" disabled={!rejectReason.trim() || actionId === rejecting.id}>{actionId === rejecting.id && <Loader2 size={15} className="animate-spin" />} {t('reject.title')}</Button></div></form></div>}
    </div>
  );
}

function CandidateDetail({
  candidate,
  actionId,
  onClose,
  onApprove,
  onReject,
  onMerge,
  live,
  canAsk = false,
  onAsk,
}: {
  candidate: Candidate;
  actionId: string | null;
  onClose: () => void;
  onApprove: (candidate: Candidate) => void;
  onReject: (candidate: Candidate) => void;
  onMerge?: (candidate: Candidate) => void;
  live?: AdviceItem;
  canAsk?: boolean;
  onAsk?: (candidate: Candidate) => void;
}) {
  const confidence = confidenceValue(candidate);
  const tags = parseList(candidate.tags);
  const sources = parseList(candidate.sources);
  const sourceNodeIds = candidate.sourceNodeIds || [];
  const isPending = candidate.status === 'pending' || candidate.status === 'pending_review' || candidate.status === 'pre_approved';
  const fullBody = candidate.body?.trim() || candidate.description?.trim() || '';
  const t = useT();

  return <div className="flex max-h-[92vh] min-h-0 flex-col">
    <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] p-5"><div className="min-w-0"><div className="flex items-center gap-2"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${statusTone(candidate.status)}`}>{statusLabel(candidate.status)}</span>{candidate.type && <span className="text-[11px] uppercase tracking-[0.12em] text-text-subtle">{candidate.type}</span>}</div><h2 id="curate-candidate-title" className="mt-3 truncate text-xl font-semibold tracking-tight text-text">{candidate.title || candidate.id}</h2><p className="mt-2 break-all font-mono text-[10px] text-text-subtle">{candidate.id}</p></div><button type="button" aria-label={t('detail.closeAria')} onClick={onClose} className="rounded-xl p-2 text-text-muted hover:bg-white/[0.06] hover:text-text"><X size={19} /></button></div>
    <div className="min-h-0 overflow-y-auto p-5"><div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
<Meta label={t('detail.created')} value={formatDate(candidate.created)} />
<Meta label={t('detail.confidence')} value={confidenceLabel(candidate) ?? '—'} />
{(candidate.status === 'pending_review' || candidate.status === 'pending' || candidate.status === 'pre_approved') ? (<>
<Meta label={t('detail.jevVerdict')} value={candidate.jev_choice ? `${candidate.jev_choice} · ${candidate.jev_confidence ? Math.round(Number(candidate.jev_confidence) * 100) + '%' : ''}` : t('detail.notClassified')} />
<Meta label={t('detail.pipeline')} value={t('detail.awaiting')} />
</>) : IN_VAULT_STATUSES.has(candidate.status) ? (<>
<Meta label={t('detail.inVault')} value={formatDate(candidate.promoted_at || candidate.created)} />
<Meta label={t('detail.jevVerdict')} value={candidate.jev_choice ? `${candidate.jev_choice} · ${candidate.jev_confidence ? Math.round(Number(candidate.jev_confidence) * 100) + '%' : ''}` : candidate.status === 'applied' ? t('detail.preGate') : t('detail.human')} />
</>) : candidate.status === 'rejected' ? (<>
<Meta label={t('detail.rejected')} value={formatDate(candidate.rejected_at)} />
<Meta label={t('detail.jevVerdict')} value={candidate.jev_choice ? candidate.jev_choice : t('detail.human')} />
</>) : candidate.status === 'auto_rejected' ? (<>
<Meta label={t('detail.autoRejected')} value={formatDate(candidate.auto_rejected_at)} />
<Meta label={t('detail.jevConfidence')} value={candidate.jev_confidence ? Math.round(Number(candidate.jev_confidence) * 100) + '%' : '—'} />
</>) : (<>
{candidate.approved_at && <Meta label={t('detail.approved')} value={formatDate(candidate.approved_at)} />}
{candidate.quarantine_until && <Meta label={t('detail.quarantine')} value={formatDate(candidate.quarantine_until)} />}
</>)}
</div>{(candidate.curator_verdict || canAsk) && <CuratorAdvicePanel fields={candidate} live={live} canAsk={canAsk && !!onAsk} onAsk={() => onAsk?.(candidate)} />}<div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">{t('detail.body')}</p>{fullBody ? <article className="max-h-[32vh] overflow-y-auto rounded-xl border border-white/[0.08] bg-black/10 p-3"><CandidateMarkdown content={fullBody} /></article> : <div className="rounded-xl border border-dashed border-amber-400/25 bg-amber-400/[0.06] p-3 text-sm leading-6 text-amber-100/80">{t('detail.metadataOnly')}</div>}</div>{candidate.sourceNotes?.length ? <div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">{t('detail.evidence')}</p><div className="space-y-3">{candidate.sourceNotes.map((note) => <section key={note.source} className="rounded-xl border border-white/[0.08] bg-black/10 p-3"><div className="flex items-center justify-between gap-3"><p className="text-sm font-medium text-text">{note.title}</p><span className={`text-[10px] uppercase tracking-[0.12em] ${note.match_type === 'related' ? 'text-amber-300' : note.found ? 'text-emerald-300' : 'text-text-subtle'}`}>{note.match_type === 'related' ? t('detail.related') : note.found ? t('detail.found') : t('detail.missing')}</span></div>{note.found && <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-text-muted">{note.body}</p>}</section>)}</div></div> : null}{sourceNodeIds.length > 0 && <div className="mt-5"><div className="mb-2"><p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">{t('detail.sourceNodes', { count: sourceNodeIds.length })}</p><p className="mt-1 text-xs leading-5 text-text-muted">{t('detail.sourceNodesHint')}</p></div><div className="space-y-1.5">{sourceNodeIds.map((nodeId) => <code key={nodeId} title={nodeId} className="block break-all rounded-lg border border-sky-400/15 bg-sky-400/[0.06] px-2.5 py-2 text-[11px] leading-5 text-sky-200">{nodeId}</code>)}</div></div>}{tags.length > 0 && <DetailList label={t('detail.tags')} items={tags} tone="sky" />}{sources.length > 0 && <DetailList label={t('detail.sourcesSummary')} items={sources} tone="neutral" />}{candidate.rejection_reason && <div className="mt-5 rounded-xl border border-rose-400/20 bg-rose-400/10 p-3"><p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-rose-300">{t('detail.rejectionFeedback')}</p><p className="mt-2 text-sm leading-5 text-rose-100/80">{candidate.rejection_reason}</p></div>}</div>
    <div className="flex justify-end gap-2 border-t border-white/[0.08] p-4"><Button variant="ghost" onClick={onClose}>{t('common.close')}</Button>{onMerge && isPending && candidate.source === 'session_synthesis' && <Button variant="secondary" disabled={!!actionId} onClick={() => onMerge(candidate)} title={t('detail.mergeTitle')}><GitMerge size={15}/> {t('detail.mergeInto')}</Button>}{isPending && <><Button variant="danger" onClick={() => onReject(candidate)} disabled={actionId === candidate.id}><XCircle size={15} /> {t('detail.reject')}</Button><Button variant="primary" onClick={() => onApprove(candidate)} disabled={actionId === candidate.id}>{actionId === candidate.id ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} {t('detail.approve')}</Button></>}</div>
  </div>;
}

function Meta({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><p className="text-[10px] uppercase tracking-[0.12em] text-text-subtle">{label}</p><p className="mt-1 truncate text-xs text-text-muted">{value}</p></div>; }
function DetailList({ label, items, tone }: { label: string; items: string[]; tone: 'sky' | 'neutral' }) { return <div className="mt-5"><p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-subtle">{label}</p><div className="flex flex-wrap gap-1.5">{items.map((item) => <span key={item} className={`rounded-lg border px-2 py-1 text-[11px] ${tone === 'sky' ? 'border-sky-400/20 bg-sky-400/10 text-sky-200' : 'border-white/10 bg-white/[0.04] text-text-muted'}`}>{item}</span>)}</div></div>; }
