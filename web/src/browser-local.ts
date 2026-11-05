type Documents = Record<string, unknown>;
type WireResponse = {status: number; headers: Record<string, string>; body: string};
type Output = {response: WireResponse; documents: Documents};
type Run = Record<string, unknown> & {id: string; status: string; canKeep: boolean; stop: Int32Array; group: string};
const runs = new Map<string, Run>();
let sequence = 0;
const pending = new Map<number, {resolve: (value: Output) => void; reject: (error: Error) => void; progress?: (value: Record<string, unknown>) => void}>();

function openStorage(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open("gate-schedule-browser-v1", 1);
    opening.onupgradeneeded = () => opening.result.createObjectStore("workspace");
    opening.onsuccess = () => resolve(opening.result);
    opening.onerror = () => reject(new Error("本机保存空间无法打开，请允许此网站保存数据后重试"));
    opening.onblocked = () => reject(new Error("请关闭其他旧版登机口页面，再重新打开"));
  });
}

async function read(database: IDBDatabase): Promise<Documents> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("workspace", "readonly");
    const request = transaction.objectStore("workspace").get("documents");
    request.onsuccess = () => resolve(request.result ?? {});
    request.onerror = () => reject(new Error("本机资料读取失败，请保留之前导出的配置文件"));
  });
}

async function save(database: IDBDatabase, documents: Documents): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction("workspace", "readwrite", {durability: "strict"});
    transaction.objectStore("workspace").put(documents, "documents");
    transaction.oncomplete = () => resolve();
    transaction.onabort = transaction.onerror = () => reject(new Error("本机保存失败，当前操作未采用；请检查可用空间后重试"));
  });
}

function toBase64(bytes: Uint8Array): string {
  let text = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) text += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(text);
}
function response(wire: WireResponse): Response {
  const bytes = Uint8Array.from(atob(wire.body), char => char.charCodeAt(0));
  return new Response(bytes, {status: wire.status, headers: wire.headers});
}
function json(status: number, value: unknown): Response { return Response.json(value, {status}); }

export async function installLocalRuntime(): Promise<void> {
  if (!crossOriginIsolated || typeof SharedArrayBuffer === "undefined") throw new Error("网页正在准备本机运行环境，请稍后刷新；需要通过 HTTPS 或本机网址打开");
  if (typeof (WebAssembly as unknown as {Suspending?: unknown}).Suspending !== "function") throw new Error("请用更新后的 Chrome 或 Edge 打开此网页，再导入配置");
  if (!navigator.locks) throw new Error("当前浏览器不支持安全保存，请使用更新后的 Chrome 或 Edge");
  const database = await openStorage();
  const worker = new Worker(new URL("./browser-worker.ts", import.meta.url), {type: "module"});
  try {
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("运行文件加载超时，请检查网络后刷新")), 120000);
    worker.onerror = event => { clearTimeout(timer); reject(new Error(event.message || "运行文件加载失败")); };
    worker.onmessage = event => {
      const message = event.data;
      if (message.kind === "ready") { clearTimeout(timer); resolve(); return; }
      if (message.kind === "startup-error") { clearTimeout(timer); reject(new Error(message.error)); return; }
      const request = pending.get(message.id);
      if (!request) return;
      if (message.kind === "progress") { request.progress?.(message.payload); return; }
      pending.delete(message.id);
      if (message.kind === "result") request.resolve(JSON.parse(message.payload));
      else request.reject(new Error(message.error));
    };
  });
  } catch (error) { worker.terminate(); database.close(); throw error; }
  worker.onerror = event => {
    for (const request of pending.values()) request.reject(new Error(event.message || "计算中断，当前操作未保存，请刷新重试"));
    pending.clear();
  };
  const transaction = async (url: string, method: string, headers: Record<string, string>, body: string, run?: Run): Promise<Response> =>
    await navigator.locks.request("gate-schedule-browser-workspace-v1", async () => {
      const documents = await read(database);
      const id = ++sequence;
      const output = await new Promise<Output>((resolve, reject) => {
        pending.set(id, {resolve, reject, progress: run ? value => Object.assign(run, value) : undefined});
        worker.postMessage({id, input: JSON.stringify({url, method, headers, body, documents, run: !!run}), stopBuffer: run?.stop.buffer});
      });
      const result = response(output.response);
      if (result.ok) await save(database, output.documents);
      return result;
    });

  const networkFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const original = input instanceof Request ? input.url : String(input);
    const url = new URL(original, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) return networkFetch(input, init);
    const request = new Request(url, input instanceof Request ? input : init);
    const method = request.method;
    const headers = Object.fromEntries(request.headers.entries());
    const bytes = new Uint8Array(await request.arrayBuffer());
    const body = toBase64(bytes);
    const route = url.pathname.match(/^\/api\/groups\/([AB])\/workspace\/runs(?:\/([^/]+)(\/stop)?)?$/);
    try {
      if (route) {
        const [, group, id, stop] = route;
        if (!id && method === "POST") {
          if ([...runs.values()].some(value => value.status === "running")) return json(409, {detail: "已有排班正在计算，请等待或停止后重试"});
          const run: Run = {id: crypto.randomUUID(), group, status: "running", canKeep: false,
            stage: "准备排班资料", stages: ["准备排班资料"], solutionCount: 0, percent: 0,
            completedSteps: 0, totalSteps: 7, steps: [], stop: new Int32Array(new SharedArrayBuffer(4))};
          runs.set(run.id, run);
          void transaction(url.pathname, method, headers, body, run).then(async result => {
            const value = await result.json();
            if (!result.ok) throw new Error(value.detail ?? "排班未保存");
            Object.assign(run, {status: "completed", stage: "草稿已保存", revision: value.revision, percent: 100, completedSteps: 7});
            run.steps = (run.steps as Record<string, unknown>[]).map(step => ({...step, status: "completed"}));
          }).catch(error => Object.assign(run, {status: Atomics.load(run.stop, 0) === 1 ? "cancelled" : "failed", message: String(error)}));
          while (runs.size > 20) { const key = [...runs].find(([, value]) => value.status !== "running")?.[0]; if (!key) break; runs.delete(key); }
          return runResponse(run);
        }
        const run = id ? runs.get(id) : undefined;
        if (!run || run.group !== group) return json(404, {detail: "计算任务不存在，请重新读取班表"});
        if (stop && method === "POST" && run.status === "running") {
          const keep = JSON.parse(new TextDecoder().decode(bytes)).keep === true;
          if (keep && !run.canKeep) return json(409, {detail: "尚无通过校验的安排，请稍候或选择停止不采用"});
          Atomics.store(run.stop, 0, keep ? 2 : 1);
          worker.postMessage({kind: "interrupt"});
        }
        return runResponse(run);
      }
      return await transaction(url.pathname + url.search, method, headers, body);
    } catch (error) { return json(503, {detail: String(error)}); }
  };
}

function runResponse(run: Run): Response {
  const {stop: _stop, group: _group, ...view} = run;
  return json(200, view);
}
