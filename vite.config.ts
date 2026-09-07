import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// gluestack ships native and DOM implementations side by side. Resolve the
// web variants consistently in both dependency prebundling and production.
const webExtensions = [
  ".web.mjs",
  ".mjs",
  ".web.js",
  ".js",
  ".web.ts",
  ".ts",
  ".web.tsx",
  ".tsx",
  ".web.jsx",
  ".jsx",
  ".json",
];

export default defineConfig({
  root: "app",
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "react-native": "react-native-web" },
    extensions: webExtensions,
  },
  optimizeDeps: { esbuildOptions: { resolveExtensions: webExtensions } },
  build: { outDir: "../dist/renderer", emptyOutDir: true },
  server: { host: "127.0.0.1", port: 5173, strictPort: true },
});
