import React, { useEffect, useRef, useState } from 'react';
import { Bell, ListChecks, Loader2 } from 'lucide-react';
import { tr, useT } from './i18n';
import { createSingleFlightPoller } from './single-flight-poller';
interface MCPluginAttentionProps {
  onActiveChange: (count: number) => void;
}

interface VaultInfo {
  id: string;
  label: string;
  pending_count?: number;
}

const API_BASE = '/api/local';

async function getJSON<T>(path: string, token: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    cache: 'no-store',
    signal,
  });
  if (!response.ok) throw new Error(tr('attention.unavailable', { status: response.status }));
  return response.json() as Promise<T>;
}

export function CurateAttention({ onActiveChange }: MCPluginAttentionProps) {
  const t = useT();
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(0);
  const [byVault, setByVault] = useState<Array<{ id: string; label: string; count: number }>>([]);
  const [loading, setLoading] = useState(true);
  // Hosts may pass a fresh callback every render; keep it out of the polling
  // effect so a re-render never restarts the poll (and never adds a request).
  const onActiveChangeRef = useRef(onActiveChange);
  useEffect(() => {
    onActiveChangeRef.current = onActiveChange;
  }, [onActiveChange]);

  useEffect(() => {
    setToken(localStorage.getItem('mission-control-token') || '');
  }, []);

  useEffect(() => {
    if (!token) return;
    const poller = createSingleFlightPoller({
      intervalMs: 30_000,
      load: (signal) => getJSON<{ vaults: VaultInfo[] }>('/candidates/vaults', token, signal),
      onResult: (vaultPayload) => {
        const counts = (vaultPayload.vaults || []).map((vault) => ({
          id: vault.id,
          label: vault.label,
          count: vault.pending_count || 0,
        }));
        const active = counts.filter((item) => item.count > 0);
        const total = active.reduce((sum, item) => sum + item.count, 0);
        setByVault(active);
        setPending(total);
        onActiveChangeRef.current(total);
        setLoading(false);
      },
      onError: () => {
        setByVault([]);
        setPending(0);
        onActiveChangeRef.current(0);
        setLoading(false);
      },
    });
    poller.start();
    return () => poller.stop();
  }, [token]);

  if (loading) return <div className="flex items-center gap-2 text-xs text-text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" /> {t('attention.loading')}</div>;
  if (pending === 0) return null;

  const href = byVault.length === 1 ? `/curate?vault=${encodeURIComponent(byVault[0].id)}` : '/curate';
  const detail = byVault.length > 0 ? ` · ${byVault.map((item) => `${item.label}: ${item.count}`).join(' · ')}` : '';

  return (
    <div className="flex items-start gap-2">
      <Bell className="h-4 w-4 flex-shrink-0 text-warning mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-text">Curate</p>
        <p className="mt-0.5 line-clamp-2 text-xs text-text-muted">{t('attention.need', { count: pending, detail })}</p>
      </div>
      <a href={href} className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--control-radius)] border border-warning/30 text-warning transition-colors hover:bg-warning/10 sm:h-auto sm:w-auto sm:gap-1 sm:px-2 sm:py-1 sm:text-xs" aria-label={t('attention.reviewAria')} title={t('attention.reviewAria')}>
        <ListChecks aria-hidden="true" className="h-4 w-4 sm:h-3 sm:w-3" />
        <span className="hidden sm:inline">{t('attention.review')}</span>
      </a>
    </div>
  );
}
