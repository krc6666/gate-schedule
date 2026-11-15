/** Start either browser mode only after the Pages service worker controls it. */
export async function ensureBrowserIsolation(): Promise<boolean> {
  const reloadKey = "gate-isolation-reload:" + location.pathname;
  if (crossOriginIsolated) { sessionStorage.removeItem(reloadKey); return true; }
  if (!isSecureContext || !navigator.serviceWorker) throw new Error("请通过 HTTPS 或本机网址打开，并允许浏览器使用服务工作线程。");
  let timer: ReturnType<typeof setTimeout> | undefined;
  let detach = () => {};
  let finished = false;
  try {
    await Promise.race([
      navigator.serviceWorker.ready.then(() => {
        if (finished) return;
        return new Promise<void>(resolve => {
          const controlled = () => { if (navigator.serviceWorker.controller) resolve(); };
          navigator.serviceWorker.addEventListener("controllerchange", controlled);
          detach = () => navigator.serviceWorker.removeEventListener("controllerchange", controlled);
          controlled();
        });
      }),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("浏览器运行环境准备失败，请检查网络后刷新。")), 15000); }),
    ]);
  } finally { finished = true; clearTimeout(timer); detach(); }
  if (sessionStorage.getItem(reloadKey)) throw new Error("浏览器隔离环境未能启用，请重新打开网页或使用更新后的 Chrome / Edge。");
  sessionStorage.setItem(reloadKey, "1");
  location.reload();
  return false;
}
