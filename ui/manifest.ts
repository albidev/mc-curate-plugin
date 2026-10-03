import type { MCPluginManifest } from './types';
export const curateManifest: MCPluginManifest = {
  id: 'curate',
  name: 'Curate',
  description: 'Review BDH session-synthesis and legacy candidate proposals',
  version: '1.2.0',
  enabled: true,
  navItem: {
    to: '/curate',
    label: 'Curate',
    icon: 'Brain',
    order: 60,
    indicator: {
      endpoint: '/curate/status',
      pollMs: 30_000,
      tones: ['neutral', 'info', 'success', 'warning', 'error'],
    },
  },
  routePath: '/curate',
  lazyRoute: true,
  surfaces: { attention: { enabled: true, order: 60 } },
  permissions: [],
  endpoints: [
    { method: 'GET', path: '/candidates', handler: 'listCandidates' },
    { method: 'GET', path: '/candidates/vaults', handler: 'listVaults' },
    { method: 'POST', path: '/candidates/approve', handler: 'approveCandidate' },
    { method: 'GET', path: '/synthesis/merge-targets', handler: 'listMergeTargets', authRequired: true },
    { method: 'POST', path: '/synthesis/merge-preview', handler: 'previewSynthesisMerge', authRequired: true },
    { method: 'POST', path: '/synthesis/merge', handler: 'mergeSynthesisCandidate', authRequired: true },
    { method: 'POST', path: '/candidates/reject', handler: 'rejectCandidate' },
  ],
};
