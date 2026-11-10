import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [react(), {
    name: 'public-shell-precache',
    apply: 'build',
    closeBundle() {
      const output = resolve(process.cwd(), 'dist')
      const assets = readdirSync(resolve(output, 'assets')).sort()
      const version = createHash('sha256').update(assets.join('\n')).digest('hex').slice(0, 12)
      const publicAssets = ['/', '/icon.svg', '/manifest.webmanifest', ...assets.map(name => `/assets/${name}`)]
      const worker = readFileSync(resolve(output, 'sw.js'), 'utf8')
        .replace('__BUILD_ID__', version)
        .replace("['__PRECACHE_ASSETS__']", JSON.stringify(publicAssets))
      writeFileSync(resolve(output, 'sw.js'), worker)
    },
  }],
  server: {
    proxy: {
      '/api': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/healthz': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000' },
    },
  },
})
