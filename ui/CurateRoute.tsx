import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { CheckCircle2, ChevronDown, History, Inbox, RefreshCw, RotateCcw, Search, ShieldCheck, XCircle } from 'lucide-react';

interface Candidate {
  id: string; title?: string; status: string; body?: string;
  approved_at?: string; rejected_at?: string; rejection_reason?: string;
  quarantine_until?: string; promoted_at?: string;
}

interface VaultInfo {
  id: string; label: string; mode: string;
  candidate_count: number; pending_count: number; reviewed_count: number;
}

const API_BASE = '/api/local';

async function fetchJSON(path: string, token?: string) {
  const res = await fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

export function CurateRoute() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [vaults, setVaults] = useState<VaultInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedVault, setSelectedVault] = useState<string>('core');
  const [token, setToken] = useState<string>('');

  useEffect(() => {
    const t = localStorage.getItem('mission-control-token') || '';
    setToken(t);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [candData, vaultData] = await Promise.all([
        fetchJSON(`/candidates?vault=${selectedVault}`, token),
        fetchJSON('/candidates/vaults', token),
      ]);
      setCandidates(candData.candidates || []);
      setVaults(vaultData.vaults || []);
    } catch (e) {
      console.error('Curate load error:', e);
    } finally {
      setLoading(false);
    }
  }, [selectedVault, token]);

  useEffect(() => { if (token) load(); }, [load, token]);

  const approve = async (id: string) => {
    await fetch(`${API_BASE}/candidates/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ id, vault: selectedVault }),
    });
    load();
  };

  const reject = async (id: string, reason: string) => {
    await fetch(`${API_BASE}/candidates/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify({ id, reason, vault: selectedVault }),
    });
    load();
  };

  const pending = candidates.filter(c => c.status === 'pending');
  const reviewed = candidates.filter(c => c.status !== 'pending');

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Curate</h1>
        <button onClick={load} className="rounded bg-sky-600 px-3 py-1 text-white hover:bg-sky-700">
          <RefreshCw size={14} className="inline mr-1" /> Refresh
        </button>
      </div>

      <div className="flex gap-2">
        {vaults.map(v => (
          <button
            key={v.id}
            onClick={() => setSelectedVault(v.id)}
            className={`rounded px-3 py-1 text-sm ${selectedVault === v.id ? 'bg-sky-600 text-white' : 'bg-slate-700 text-slate-300'}`}
          >
            {v.label} ({v.pending_count})
          </button>
        ))}
      </div>

      {loading ? (
        <div className="text-slate-400">Loading...</div>
      ) : (
        <>
          <div>
            <h2 className="mb-2 text-lg font-semibold">Pending ({pending.length})</h2>
            <div className="flex flex-col gap-2">
              {pending.map(c => (
                <div key={c.id} className="rounded-lg border border-slate-600 bg-slate-800 p-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="font-medium">{c.title || c.id}</h3>
                      <p className="mt-1 text-sm text-slate-400 line-clamp-3">{c.body}</p>
                    </div>
                    <div className="flex gap-2 ml-4">
                      <button onClick={() => approve(c.id)} className="rounded bg-emerald-600 p-2 text-white hover:bg-emerald-700">
                        <CheckCircle2 size={16} />
                      </button>
                      <button onClick={() => reject(c.id, prompt('Reason') || '')} className="rounded bg-red-600 p-2 text-white hover:bg-red-700">
                        <XCircle size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="mb-2 text-lg font-semibold">Reviewed ({reviewed.length})</h2>
            <div className="flex flex-col gap-2">
              {reviewed.map(c => (
                <div key={c.id} className="rounded-lg border border-slate-700 bg-slate-800/50 p-3 opacity-70">
                  <div className="flex items-center justify-between">
                    <span className="text-sm">{c.title || c.id}</span>
                    <span className={`text-xs px-2 py-0.5 rounded ${c.status === 'approved' ? 'bg-sky-600/20 text-sky-400' : c.status === 'rejected' ? 'bg-red-600/20 text-red-400' : 'bg-slate-600/20 text-slate-400'}`}>
                      {c.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
