import { defineConfig, loadEnv } from "vite";
import {renameSync} from "node:fs";
import {resolve} from "node:path";
import {cloudConfiguration} from "./src/cloud-config";

export default defineConfig(({mode}) => {
  const browser = mode === "browser" || mode === "cloud";
  const env = loadEnv(mode, process.cwd(), "VITE_");
  cloudConfiguration(env); // Reject private keys before they can enter an artifact.
  let outputDirectory = "";
  if (mode === "cloud" && (!env.VITE_SUPABASE_URL || !env.VITE_SUPABASE_PUBLISHABLE_KEY)) throw new Error("Cloud build requires the Supabase URL and public key");
  return {
  define: {"import.meta.env.VITE_CLOUD_REQUIRED": JSON.stringify(mode === "cloud")},
  base: browser ? "./" : "/",
  publicDir: browser ? "../.tmp/browser-public" : false,
  optimizeDeps: {include: ["protobufjs"], exclude: ["cp-sat-wasm"]},
  worker: {format: "es"},
  build: browser ? {outDir: mode === "cloud" ? "../build/cloud" : "../build/browser", emptyOutDir: true, rollupOptions: {input: "browser.html"}} : {},
  plugins: browser ? [{name: "browser-index", apply: "build", configResolved(config) {outputDirectory = config.build.outDir;}, closeBundle() {renameSync(resolve(outputDirectory,"browser.html"), resolve(outputDirectory,"index.html"));}}] : [],
  server: {
    headers: browser ? {"Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp"} : {},
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
}; });
