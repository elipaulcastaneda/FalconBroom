import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite config with a dev proxy for backend API requests.
// Proxy `/api/*` to `http://127.0.0.1:3009` during development to avoid CORS issues.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 1421,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:3009',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
