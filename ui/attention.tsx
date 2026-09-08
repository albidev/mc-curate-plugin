import React, { useEffect, useState } from 'react';
import { Bell, ListChecks, Loader2 } from 'lucide-react';
interface MCPluginAttentionProps {
  onActiveChange: (count: number) => void;
}

interface VaultInfo {
  id: string;
  label: string;
}

interface CandidatesPayload {
  count?: number;
}

const API_BASE = '/api/local';

async function getJSON<T>(path: string, token: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
  });
  if (!response.ok) throw new Error(`Curate attention unavailable (${response.status})`);
  return response.json() as Promise<T>;
}

export function CurateAttention({ onActiveChange }: MCPluginAttentionProps) {
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(0);
  const [byVault, setByVault] = useState<Array<{ id: string; label: string; count: number }>>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setToken(localStorage.getItem('mission-control-token') || '');
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const vaultPayload = await getJSON<{ vaults: VaultInfo[] }>('/candidates/vaults', token);
        const counts = await Promise.all((vaultPayload.vaults || []).map(async (vault) => {
          const payload = await getJSON<CandidatesPayload>(`/candidates?status=pending_review&vault=${encodeURIComponent(vault.id)}`, token);
          return { id: vault.id, label: vault.label, count: payload.count || 0 };
        }));
        if (cancelled) return;
        const active = counts.filter((item) => item.count > 0);
        const total = active.reduce((sum, item) => sum + item.count, 0);
        setByVault(active);
        setPending(total);
        onActiveChange(total);
      } catch {
        if (!cancelled) {
          setByVault([]);
          setPending(0);
          onActiveChange(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [onActiveChange, token]);

  if (loading) return <div className="flex items-center gap-2 text-xs text-text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading plugin attention…</div>;
  if (pending === 0) return null;

  const href = byVault.length === 1 ? `/curate?vault=${encodeURIComponent(byVault[0].id)}` : '/curate';
  const detail = byVault.length > 0 ? ` · ${byVault.map((item) => `${item.label}: ${item.count}`).join(' · ')}` : '';

  return (
    <div className="flex items-start gap-2">
      <Bell className="h-4 w-4 flex-shrink-0 text-warning mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text">Curate</p>
        <p className="mt-0.5 line-clamp-2 text-xs text-text-muted">{pending} candidate{pending === 1 ? '' : 's'} need review{detail}</p>
      </div>
      <a href={href} className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--control-radius)] border border-warning/30 text-warning transition-colors hover:bg-warning/10 sm:h-auto sm:w-auto sm:gap-1 sm:px-2 sm:py-1 sm:text-xs" aria-label="Review Curate candidates" title="Review Curate candidates">
        <ListChecks aria-hidden="true" className="h-4 w-4 sm:h-3 sm:w-3" />
        <span className="hidden sm:inline">Review</span>
      </a>
    </div>
  );
}
