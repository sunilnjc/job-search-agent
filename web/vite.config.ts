import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  // The deployed FastAPI service and its local secrets use the repository root.
  // Only VITE_ values are exposed to the browser; service credentials remain hidden.
  envDir: '..',
  plugins: [react()],
})
