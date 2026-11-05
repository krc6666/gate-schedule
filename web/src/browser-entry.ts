import {installLocalRuntime} from "./browser-local";

async function start(): Promise<void> {
  const root = document.querySelector<HTMLDivElement>("#app")!;
  root.textContent = "正在打开登机口排班，首次打开需要加载运行文件，请稍候…";
  try {
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
