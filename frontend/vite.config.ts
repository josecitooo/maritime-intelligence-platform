import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // The backend already allows this origin in CORS (`app/main.py`).
    port: 5173,
    strictPort: true,
  },
})
