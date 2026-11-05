import { defineConfig } from "vite";
import {renameSync} from "node:fs";
import {resolve} from "node:path";

export default defineConfig(({mode}) => ({
  base: mode === "browser" ? "./" : "/",
  publicDir: mode === "browser" ? "../.tmp/browser-public" : false,
  optimizeDeps: {include: ["protobufjs"], exclude: ["cp-sat-wasm"]},
  worker: {format: "es"},
  build: mode === "browser" ? {outDir: "../build/browser", emptyOutDir: true, rollupOptions: {input: "browser.html"}} : {},
  plugins: mode === "browser" ? [{name: "browser-index", apply: "build", closeBundle() {renameSync(resolve("../build/browser/browser.html"), resolve("../build/browser/index.html"));}}] : [],
  server: {
    headers: mode === "browser" ? {"Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp"} : {},
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
}));
