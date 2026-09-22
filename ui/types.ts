import type React from 'react';

export interface MCPluginManifest {
  id: string; name: string; description: string; version: string;
  enabled?: boolean; navItem?: MCPluginNavItem; routePath?: string;
  lazyRoute?: boolean; permissions?: string[]; endpoints?: MCPluginEndpoint[];
  surfaces?: { attention?: { enabled?: boolean; order?: number } };
  configSchema?: Record<string, unknown>;
}
export interface MCPluginNavIndicator { endpoint: string; pollMs?: number; tones: Array<'neutral' | 'info' | 'success' | 'warning' | 'error'>; }
export interface MCPluginNavItem { to: string; label: string; icon: string; indicator?: MCPluginNavIndicator; showWhen?: (ctx: any) => boolean; order?: number; }
export interface MCPluginEndpoint { method: 'GET' | 'POST' | 'PUT' | 'DELETE'; path: string; handler: string; authRequired?: boolean; }
export interface MCPluginRoute { path: string; element: React.ReactElement; index?: boolean; }
