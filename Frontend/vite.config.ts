import path from "path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  publicDir: "favicon_io",
  test: {
    environment: "node",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/webrtc": {
        target: "http://localhost:1984",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/webrtc/, ""),
        ws: true,
      },
    },
  },
});
