import { cpSync, existsSync } from "node:fs";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// Excalidraw's fonts are self-hosted (no CDN): copy them into public/ before serving/building.
function excalidrawFonts(): Plugin {
  return {
    name: "excalidraw-fonts",
    buildStart() {
      const src = "node_modules/@excalidraw/excalidraw/dist/prod/fonts";
      if (existsSync(src) && !existsSync("public/fonts")) cpSync(src, "public/fonts", { recursive: true });
    },
  };
}

const backend = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), excalidrawFonts()],
  server: {
    port: 5173,
    proxy: { "/api": { target: backend, changeOrigin: true }, "/files": backend },
  },
  build: { outDir: "dist", chunkSizeWarningLimit: 4000 },
});
