import React, { useEffect, useRef, useState } from 'react';
import { Check, GitMerge, Loader2, X } from 'lucide-react';

interface Target { node_id: string; title: string; note_path: string }
interface Targets { vault_id: string; targets: Target[]; suggested_target_node_id: string | null; suggested_target_error: string | null; total_count: number; has_more: boolean }
interface Preview { vault_id: string; candidate: { candidate_id: string; title: string; definition: string }; target: Target & { content: string }; candidate_revision: string; target_revision: string }
export interface MergeResult { status: 'merged' | 'noop'; note_path: string; operation_id: string; applied: boolean; idempotent: boolean }
type Request = <T>(path: string, init?: RequestInit) => Promise<T>;

export function MergeDialog({ candidate, request, onCancel, onSuccess }: {
  candidate: { id: string; title?: string; vault_id: string; curator_merge_target?: string; curator_note?: string };
  request: Request; onCancel: () => void; onSuccess: (result: MergeResult) => void;
}) {
  const [query, setQuery] = useState('');
  const [listing, setListing] = useState<Targets | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [searching, setSearching] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const inFlight = useRef(false);
  const cancel = useRef(onCancel);
  cancel.current = onCancel;
  const identity = { candidate_id: candidate.id, vault: candidate.vault_id };

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.querySelector<HTMLInputElement>('input')?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!inFlight.current) cancel.current(); }
      if (event.key !== 'Tab') return;
      const focusable = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled)') || []);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && (document.activeElement === first || !dialog.current?.contains(document.activeElement))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !dialog.current?.contains(document.activeElement))) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', keyboard, true);
    return () => { document.removeEventListener('keydown', keyboard, true); if (previous?.isConnected) previous.focus(); };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setSearching(true); setError(null); setListing(null);
    const params = new URLSearchParams({ ...identity, q: query });
    request<Targets>(`/synthesis/merge-targets?${params}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.vault_id !== candidate.vault_id || !Array.isArray(data.targets)) throw new Error('Invalid merge target response.');
        setListing(data);
        if (!query && !selected && data.suggested_target_node_id && data.targets.some(t => t.node_id === data.suggested_target_node_id)) setSelected(data.suggested_target_node_id);
      })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load target notes.'); })
      .finally(() => { if (!controller.signal.aborted) setSearching(false); });
    return () => controller.abort();
  }, [query, candidate.id, candidate.vault_id, request]);

  useEffect(() => {
    setPreview(null);
    if (!selected) { setPreviewing(false); return; }
    const controller = new AbortController();
    setPreviewing(true);
    request<Preview>('/synthesis/merge-preview', { method: 'POST', signal: controller.signal,
      body: JSON.stringify({ ...identity, target_node_id: selected }) })
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.vault_id !== candidate.vault_id || data.candidate.candidate_id !== candidate.id || data.target.node_id !== selected || !data.candidate_revision || !data.target_revision) throw new Error('Preview does not match this candidate and target.');
        setPreview(data);
      })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not preview merge.'); })
      .finally(() => { if (!controller.signal.aborted) setPreviewing(false); });
    return () => controller.abort();
  }, [selected, reload, candidate.id, candidate.vault_id, request]);

  const confirm = async () => {
    if (inFlight.current || !preview || searching || previewing || preview.target.node_id !== selected) return;
    inFlight.current = true; setSubmitting(true); setError(null);
    try {
      const result = await request<MergeResult>('/synthesis/merge', { method: 'POST', body: JSON.stringify({
        ...identity, target_node_id: preview.target.node_id, candidate_revision: preview.candidate_revision,
        target_revision: preview.target_revision, confirmed: true,
      }) });
      if (!['merged', 'noop'].includes(result.status)) throw new Error('BDH did not confirm a merge outcome. No automatic approval was attempted.');
      onSuccess(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Merge failed.');
      setPreview(null); // A retry requires a fresh preview and another human click.
    } finally { inFlight.current = false; setSubmitting(false); }
  };

  return <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-3 sm:p-6" onMouseDown={event => { if (event.target === event.currentTarget && !inFlight.current) onCancel(); }}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby="merge-title" className="flex max-h-[90dvh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-white/10 bg-surface shadow-2xl">
      <header className="flex shrink-0 items-start justify-between gap-3 border-b border-white/[0.08] p-4">
        <div className="min-w-0"><h2 id="merge-title" className="flex items-center gap-2 text-base font-semibold text-text"><GitMerge size={18}/> Merge into existing note</h2><p className="mt-1 break-words text-sm text-text-muted">{candidate.title || candidate.id} · {candidate.vault_id}</p></div>
        <button type="button" aria-label="Close merge dialog" disabled={submitting} onClick={onCancel} className="rounded-lg p-2 text-text-muted disabled:opacity-40"><X size={18}/></button>
      </header>
      <div className="min-h-0 overflow-y-auto p-4">
        {candidate.curator_merge_target && <p className="mb-2 break-words text-xs text-sky-200">Suggested merge target: {candidate.curator_merge_target}</p>}
        {candidate.curator_note && <p className="mb-3 text-xs text-text-muted">{candidate.curator_note}</p>}
        <label className="block text-xs font-medium text-text-muted">Search notes in this vault<input value={query} disabled={submitting} onChange={event => { setPreview(null); setSelected(null); setQuery(event.target.value); }} placeholder="Title or filename…" className="mt-2 h-10 w-full rounded-xl border border-white/10 bg-black/10 px-3 text-sm text-text outline-none focus:border-sky-400/40"/></label>
        {listing?.suggested_target_error && <p role="status" className="mt-3 rounded-xl border border-amber-400/25 bg-amber-400/10 p-3 text-xs text-amber-200">{listing.suggested_target_error}</p>}
        <div className="mt-3 max-h-40 space-y-2 overflow-y-auto" aria-label="Merge target notes">
          {searching ? <p role="status" className="flex items-center gap-2 text-sm text-text-muted"><Loader2 size={14} className="animate-spin"/> Loading target notes…</p> : listing?.targets.map(target => <button type="button" key={target.node_id} disabled={submitting} aria-pressed={selected === target.node_id} onClick={() => { setError(null); setPreview(null); setSelected(target.node_id); setReload(n => n + 1); }} className={`w-full rounded-xl border p-3 text-left ${selected === target.node_id ? 'border-sky-400/40 bg-sky-400/10' : 'border-white/10 bg-white/[0.025] hover:bg-white/[0.05]'}`}><span className="block text-sm font-medium text-text">{target.title}</span><span className="block break-all text-xs text-text-muted">{target.note_path}</span></button>)}
          {!searching && listing && !listing.targets.length && <p className="text-sm text-text-muted">No writable vault notes match.</p>}
        </div>
        {listing?.has_more && <p className="mt-2 text-xs text-text-muted">Showing {listing.targets.length} of {listing.total_count} matches. Refine the search.</p>}
        {previewing && <p role="status" className="mt-4 text-sm text-text-muted">Loading preview…</p>}
        {preview && <div className="mt-4 grid min-w-0 gap-3 md:grid-cols-2"><section className="min-w-0"><h3 className="mb-2 text-xs font-semibold text-text-muted">Existing note · {preview.target.title}</h3><pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-white/10 bg-black/10 p-3 text-xs leading-5 text-text">{preview.target.content}</pre></section><section className="min-w-0"><h3 className="mb-2 text-xs font-semibold text-sky-200">Evidence to append · {preview.candidate.title}</h3><pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-sky-400/20 bg-sky-400/[0.04] p-3 text-xs leading-5 text-text">{preview.candidate.definition}</pre><p className="mt-2 text-xs text-text-muted">Existing content is preserved. BDH adds synthesis provenance and a reversible operation record. Evidence already present results in a no-op.</p></section></div>}
        {error && <div role="alert" className="mt-3 rounded-xl border border-rose-400/25 bg-rose-400/10 p-3 text-sm text-rose-200"><p>{error}</p>{selected && <button type="button" disabled={submitting} onClick={() => { setError(null); setReload(n => n + 1); }} className="mt-2 underline">Reload preview</button>}</div>}
      </div>
      <footer className="flex shrink-0 flex-wrap justify-end gap-2 border-t border-white/[0.08] p-4">
        <button type="button" disabled={submitting} onClick={onCancel} className="rounded-xl border border-white/10 px-4 py-2 text-sm text-text disabled:opacity-40">Cancel</button>
        <button type="button" disabled={!preview || previewing || searching || submitting} onClick={() => void confirm()} className="inline-flex items-center gap-2 rounded-xl border border-emerald-400/30 bg-emerald-400/15 px-4 py-2 text-sm font-medium text-emerald-200 disabled:cursor-not-allowed disabled:opacity-40">{submitting ? <Loader2 size={14} className="animate-spin"/> : <Check size={14}/>} Confirm merge</button>
      </footer>
    </div>
  </div>;
}
