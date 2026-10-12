import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    // Nothing inlined as data: URIs. The page's security policy loads fonts
    // only from the page's own address, and a small font slipped in as data:
    // would be refused.
    assetsInlineLimit: 0,
    // The terminal (xterm.js and its WebGL renderer) makes the script
    // large; it comes from this computer, so one file is fine.
    chunkSizeWarningLimit: 1500,
    // Stable file names: every release archive overwrites the same files, so
    // nothing piles up in web/dist, and the core tells the browser to check
    // for a newer copy each time. Hashed names come with the server in stage 2.
    rolldownOptions: {
      output: {
        entryFileNames: "assets/[name].js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
