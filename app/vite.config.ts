import { defineConfig } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

export default defineConfig({
  plugins: [viteSingleFile()],
  build: {
    minify: true,
    cssMinify: true,
    rollupOptions: { input: 'manager.html' },
    outDir: 'dist',
    emptyOutDir: true,
  },
})
