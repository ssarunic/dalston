import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig(({ command }) => ({
  plugins: [react()],
  // Use /console/ base path for production build, / for dev server
  base: command === 'build' ? '/console/' : '/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/v1': {
        target: 'http://gateway:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://gateway:8000',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://gateway:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://gateway:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
}))
