export interface MCPluginManifest {
  id: string; name: string; description: string; version: string;
  enabled?: boolean; navItem?: MCPluginNavItem; routePath?: string;
  lazyRoute?: boolean; permissions?: string[]; endpoints?: MCPluginEndpoint[];
  configSchema?: Record<string, unknown>;
}
export interface MCPluginNavItem { to: string; label: string; icon: string; showWhen?: (ctx: any) => boolean; order?: number; }
export interface MCPluginEndpoint { method: 'GET' | 'POST' | 'PUT' | 'DELETE'; path: string; handler: string; authRequired?: boolean; }
export interface MCPluginRoute { path: string; element: React.ReactElement; index?: boolean; }
