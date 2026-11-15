/** Public-only configuration, shared by the build gate and browser entry. */
export function cloudConfiguration(env: Record<string, string | undefined>): {url: string; key: string} | null {
  const url = env.VITE_SUPABASE_URL?.trim() ?? "";
  const key = env.VITE_SUPABASE_PUBLISHABLE_KEY?.trim() ?? "";
  if (!url && !key) return null;
  if (!url || !key) throw new Error("云端配置不完整，需要项目URL及公开密钥。");
  let parsed: URL;
  try { parsed = new URL(url); } catch { throw new Error("Supabase项目URL无效。"); }
  if ((parsed.protocol !== "https:" && !(parsed.protocol === "http:" && ["127.0.0.1", "localhost"].includes(parsed.hostname))) || parsed.username || parsed.password || parsed.pathname !== "/" || parsed.search || parsed.hash) throw new Error("Supabase项目URL必须是HTTPS根地址。");
  let publicKey = /^sb_publishable_[\w-]+$/.test(key);
  if (!publicKey && key.split(".").length === 3) {
    try { publicKey = JSON.parse(atob(key.split(".")[1].replaceAll("-", "+").replaceAll("_", "/"))).role === "anon"; } catch { /* Never echo keys. */ }
  }
  if (!publicKey) throw new Error("只允许publishable或anon公开密钥，禁止使用私密密钥。");
  return {url: parsed.origin, key};
}
