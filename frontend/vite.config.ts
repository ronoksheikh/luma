import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// In dev, the backend runs on :8080; set LUMA_DEV_ORIGIN=http://localhost:5173 on the backend.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8080", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8080", ws: true },
      "/healthz": "http://127.0.0.1:8080",
    },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1500 },
});
