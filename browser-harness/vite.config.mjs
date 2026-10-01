import { createRequire } from 'node:module';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
const directory = path.dirname(fileURLToPath(import.meta.url));
const pluginRoot = path.resolve(directory, '..');
const hostRoot = process.env.CURATE_MC_ROOT || path.resolve(pluginRoot, '../hermes-mission-control');
const require = createRequire(path.join(hostRoot, 'package.json'));
const { defineConfig } = await import(require.resolve('vite'));
const react = (await import(require.resolve('@vitejs/plugin-react'))).default;
const tailwind = (await import(require.resolve('@tailwindcss/postcss'))).default;
export default defineConfig({
  cacheDir: path.join(process.env.CURATE_TEST_SCRATCH || path.join(os.homedir(), '.hermes/cache/scratch'), 'curate-browser-vite-cache'),
  plugins: [react(), { name: 'watch-plugin-under-test', configureServer(server) { server.watcher.add(path.join(pluginRoot, 'ui')); } }],
  resolve: {
    alias: [
      ...['react', 'react/jsx-dev-runtime', 'react/jsx-runtime', 'react-dom', 'react-dom/client', 'react-router-dom', 'lucide-react', 'react-markdown', 'remark-breaks', 'remark-gfm']
        .map(name => ({ find: new RegExp('^' + name + '$'), replacement: require.resolve(name) })),
      { find: 'host-styles', replacement: path.join(hostRoot, 'src/styles.css') },
    ],
    dedupe: ['react', 'react-dom'],
  },
  server: { host: '127.0.0.1', port: 5269, strictPort: true, fs: { allow: [pluginRoot, hostRoot] } },
  css: { postcss: { plugins: [tailwind()] } },
});
