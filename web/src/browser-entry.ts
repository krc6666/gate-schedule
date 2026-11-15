import {installLocalRuntime} from "./browser-local";
import {cloudConfiguration} from "./cloud-config";
import {ensureBrowserIsolation} from "./browser-isolation";

async function start(): Promise<void> {
  const root = document.querySelector<HTMLDivElement>("#app")!;
  root.textContent = "正在打开登机口排班，首次打开需要加载运行文件，请稍候…";
  try {
    if (!await ensureBrowserIsolation()) return;
    const configuration = import.meta.env.VITE_CLOUD_REQUIRED ? cloudConfiguration({
      VITE_SUPABASE_URL: import.meta.env.VITE_SUPABASE_URL,
      VITE_SUPABASE_PUBLISHABLE_KEY: import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY,
    }) : null;
    if (import.meta.env.VITE_CLOUD_REQUIRED && !configuration) throw new Error("此云端版本尚未配置Supabase，未回退为本机保存。");
    if (configuration) { await (await import("./cloud-entry")).startCloud(configuration); return; }
    await installLocalRuntime();
    document.documentElement.dataset.gateLocal = "true";
    await import("./workbench");
  } catch (error) {
    root.replaceChildren();
    const message = document.createElement("p"); message.textContent = String(error);
    const retry = document.createElement("button"); retry.textContent = "重新打开"; retry.onclick = () => location.reload();
    root.append(message, retry);
  }
}
void start();
