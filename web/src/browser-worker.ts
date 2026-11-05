import {loadPyodide, type PyodideInterface} from "pyodide";
import {interruptCp, solveCp} from "./browser-cp";

type RequestMessage = {id: number; input: string; stopBuffer?: SharedArrayBuffer};
const runtime = new URL("../runtime/", self.location.href).href;
let py: PyodideInterface;
let activeStop: Int32Array | null = null;
let activeId: number | null = null;

async function initialize(): Promise<void> {
  py = await loadPyodide({indexURL: runtime + "pyodide/"});
  await py.loadPackage(["sqlite3", "fastapi"]);
  for (const name of ["et_xmlfile-2.0.0-py3-none-any.whl", "openpyxl-3.1.5-py2.py3-none-any.whl"]) {
    const response = await fetch(runtime + "pyodide/" + name);
    if (!response.ok) throw new Error("网页运行文件缺失：" + name);
    py.unpackArchive(await response.arrayBuffer(), "zip", {extractDir: "/lib/python3.13/site-packages"});
  }
  const source = await fetch(runtime + "gate_schedule.zip");
  if (!source.ok) throw new Error("排班规则文件缺失");
  py.unpackArchive(await source.arrayBuffer(), "zip", {extractDir: "/lib/python3.13/site-packages"});
  py.registerJsModule("gate_browser_js", {
    solve_cp: solveCp,
    interrupt_cp: interruptCp,
    progress: (raw: string) => self.postMessage({kind: "progress", id: activeId, payload: JSON.parse(raw)}),
    stopped: () => !!activeStop && Atomics.load(activeStop, 0) !== 0,
    keeping: () => !!activeStop && Atomics.load(activeStop, 0) === 2,
  });
}

const ready = initialize().then(() => self.postMessage({kind: "ready"})).catch(error => self.postMessage({kind: "startup-error", error: String(error)}));
self.onmessage = async event => {
  if (event.data.kind === "interrupt") { interruptCp(); return; }
  const message = event.data as RequestMessage;
  await ready;
  try {
    activeId = message.id;
    activeStop = message.stopBuffer ? new Int32Array(message.stopBuffer) : null;
    py.runPython("from gate_schedule.browser_runtime import request_sync");
    const dispatch = py.globals.get("request_sync");
    let output: string;
    try { output = await dispatch.callPromising(message.input); } finally { dispatch.destroy(); }
    self.postMessage({kind: "result", id: message.id, payload: output});
  } catch (error) { self.postMessage({kind: "error", id: message.id, error: String(error)}); }
  finally { activeId = null; activeStop = null; }
};
