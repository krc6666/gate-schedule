import { defineConfig, loadEnv } from "vite";
import {createHash} from "node:crypto";
import {existsSync, readFileSync, renameSync} from "node:fs";
import {resolve} from "node:path";
import {cloudConfiguration} from "./src/cloud-config";

export default defineConfig(({mode}) => {
  const browser = mode === "browser" || mode === "cloud";
  const env = loadEnv(mode, process.cwd(), "VITE_");
  cloudConfiguration(env); // Reject private keys before they can enter an artifact.
  const runtimeArchive = resolve(process.cwd(), "../.tmp/browser-public/runtime/gate_schedule.zip");
  const runtimeVersion = existsSync(runtimeArchive)
    ? createHash("sha256").update(readFileSync(runtimeArchive)).digest("hex").slice(0, 12)
    : "development";
  let outputDirectory = "";
  if (mode === "cloud" && (!env.VITE_SUPABASE_URL || !env.VITE_SUPABASE_PUBLISHABLE_KEY)) throw new Error("Cloud build requires the Supabase URL and public key");
  return {
  define: {
    "import.meta.env.VITE_CLOUD_REQUIRED": JSON.stringify(mode === "cloud"),
    "import.meta.env.VITE_GATE_RUNTIME_VERSION": JSON.stringify(runtimeVersion),
  },
  base: browser ? "./" : "/",
  publicDir: browser ? "../.tmp/browser-public" : false,
  optimizeDeps: {include: ["protobufjs", ...(browser ? ["pyodide", "@supabase/supabase-js"] : [])], exclude: ["cp-sat-wasm"]},
  worker: {format: "es"},
  build: browser ? {outDir: mode === "cloud" ? "../build/cloud" : "../build/browser", emptyOutDir: true, rollupOptions: {input: "browser.html"}} : {},
  plugins: browser ? [{name: "browser-dev-index", apply: "serve", configureServer(server) {
    server.middlewares.use((request, _response, next) => {
      if (request.url?.split("?")[0] === "/") request.url = request.url.replace("/", "/browser.html");
      next();
    });
  }}, {name: "browser-index", apply: "build", configResolved(config) {outputDirectory = config.build.outDir;}, closeBundle() {renameSync(resolve(outputDirectory,"browser.html"), resolve(outputDirectory,"index.html"));}}] : [],
  server: {
    headers: browser ? {"Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp"} : {},
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
}; });
