import type { MCPluginManifest } from './types';
export const curateManifest: MCPluginManifest = {
  id: 'curate',
  name: 'Curate',
  description: 'Nightly brain candidate approval queue',
  version: '1.2.0',
  enabled: true,
  navItem: {
    to: '/curate',
    label: 'nav.curate',
    icon: 'ClipboardCheck',
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
    { method: 'POST', path: '/candidates/reject', handler: 'rejectCandidate' },
  ],
};
