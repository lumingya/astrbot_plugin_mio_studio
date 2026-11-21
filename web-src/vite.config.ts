import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  plugins: [{
    name: "mio-file-preview",
    apply: "build",
    transformIndexHtml(html) {
      // Offline previews can be opened directly from disk. With one IIFE
      // entry and no imports, the browser no longer requires HTTP CORS to
      // resolve a module URL under file://.
      return html.replace(/<script\b[^>]*\btype="module"[^>]*>/, tag =>
        tag.replace(/\s+type="module"/, "").replace(/\s+crossorigin(?:="[^"]*")?/, "")
          .replace(/^<script/, "<script defer")
      );
    },
  }],
  build: {
    outDir: "../pages/mio-studio",
    emptyOutDir: true,
    target: "es2022",
    modulePreload: false,
    rollupOptions: {
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "assets/panel.js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
});
