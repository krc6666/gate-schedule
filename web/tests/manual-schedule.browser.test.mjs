import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, readFile, mkdir, rm } from "node:fs/promises";
import { createServer } from "node:http";

import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { after, before, test } from "node:test";
import { build } from "esbuild";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const browserPath = [
  process.env.GATE_TEST_BROWSER,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
].find((candidate) => candidate && existsSync(candidate));

const delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));
async function waitFor(check, description) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await delay(25);
  }
  throw new Error(`等待超时：${description}`);
}

class DevTools {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 0;
    this.pending = new Map();
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
      else pending.resolve(message.result);
    });
  }

  call(method, params = {}, sessionId) {
    const id = ++this.nextId;
    return new Promise((resolveCall, reject) => {
      this.pending.set(id, { resolve: resolveCall, reject });
      this.socket.send(JSON.stringify({ id, method, params, sessionId }));
    });
  }

  async evaluate(sessionId, expression) {
    const result = await this.call("Runtime.evaluate", {
      expression, returnByValue: true, awaitPromise: true,
    }, sessionId);
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
}

function scenario(assignmentSource = "manual") {
  return {
    serviceDate: "2026-08-02",
    assignmentSource,
    flights: [{ id: "flight", flightNo: "CA100", windowStart: "08:00", windowEnd: "10:00" }],
    members: ["甲", "乙", "丙", "丁"].map((name, index) => ({
      id: `member-${index}`, name, eligibleRoleIds: ["receive", "verify"],
    })),
    tasks: [
      { id: "pair", flightId: "flight", roleId: "receive", start: "08:00", end: "08:30", requiredCount: 2, taskKind: "mobile", lockedMemberIds: [], serviceStage: "arrival" },
      { id: "other", flightId: "flight", roleId: "verify", start: "09:00", end: "09:30", requiredCount: 1, taskKind: "mobile", lockedMemberIds: [], serviceStage: "boarding" },
    ],
    assignments: [
      { taskId: "pair", memberId: "member-0" },
      { taskId: "pair", memberId: "member-1" },
      { taskId: "other", memberId: "member-3" },
    ],
    externalSupportRequirements: [], memberPaths: {},
  };
}

let server;
let browser;
let profile;
let client;
let baseUrl;
let currentScenario;
let requests;
const unexpectedRequests = [];

before(async () => {
  assert.ok(browserPath, "需要 Chrome/Edge；其他安装位置可设置 GATE_TEST_BROWSER");
  const bundle = await build({
    entryPoints: [join(webRoot, "src/main.ts")],
    bundle: true, write: false, outdir: "memory", format: "esm", platform: "browser",
  });
  const script = bundle.outputFiles.find((file) => file.path.endsWith(".js")).text;
  server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname === "/") {
      response.setHeader("Content-Type", "text/html; charset=utf-8");
      response.end('<!doctype html><html><meta charset="utf-8"><div id="app"></div><script type="module" src="/main.js"></script></html>');
      return;
    }
    if (url.pathname === "/main.js") {
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.end(script);
      return;
    }
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    if (url.pathname === "/api/schedule") return response.end(JSON.stringify(currentScenario));
    if (url.pathname === "/api/baggage-rotation-settings") {
      return response.end(JSON.stringify({ enabled: true, intervalMinutes: 60, segments: [] }));
    }
    if (["/api/validate-manual-assignments", "/api/manual-assignments"].includes(url.pathname)) {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      requests.push({ path: url.pathname, payload });
      if (url.pathname === "/api/manual-assignments") {
        currentScenario = { ...currentScenario, assignments: payload.assignments, assignmentSource: "manual" };
        return response.end(JSON.stringify(currentScenario));
      }
      return response.end(JSON.stringify({ valid: true, issues: [], memberPaths: {} }));
    }
    if (url.pathname !== "/favicon.ico") unexpectedRequests.push(url.pathname);
    response.statusCode = 404;
    response.end("{}");
  });
  await new Promise((resolveListen) => server.listen(0, "127.0.0.1", resolveListen));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
  const temporaryRoot = fileURLToPath(new URL("../../.tmp/", import.meta.url));
  await mkdir(temporaryRoot, { recursive: true });
  profile = await mkdtemp(join(temporaryRoot, "gate-schedule-browser-test-"));
  browser = spawn(browserPath, [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "about:blank",
  ], { windowsHide: true, stdio: "ignore" });
  const portFile = await waitFor(async () => {
    try { return await readFile(join(profile, "DevToolsActivePort"), "utf8"); }
    catch { return false; }
  }, "浏览器调试端口");
  const [port, websocketPath] = portFile.trim().split(/\r?\n/);
  const socket = new WebSocket(`ws://127.0.0.1:${port}${websocketPath}`);
  await new Promise((resolveOpen, reject) => {
    socket.addEventListener("open", resolveOpen, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  client = new DevTools(socket);
});

after(async () => {
  client?.socket.close();
  if (browser) {
    const exited = new Promise((resolveExit) => browser.once("exit", resolveExit));
    browser.kill();
    await Promise.race([exited, delay(3_000)]);
  }
  if (server) await new Promise((resolveClose) => server.close(resolveClose));
  if (profile) {
    assert.equal(dirname(resolve(profile)), resolve(fileURLToPath(new URL("../../.tmp/", import.meta.url))), "仅清理本测试创建的临时浏览器目录");
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
  assert.deepEqual(unexpectedRequests, [], "测试页面不应连接测试桩以外的业务接口");
});

async function openPage(t, source = "manual") {
  currentScenario = scenario(source);
  requests = [];
  const { targetId } = await client.call("Target.createTarget", { url: "about:blank" });
  t.after(() => client.call("Target.closeTarget", { targetId }));
  const { sessionId } = await client.call("Target.attachToTarget", { targetId, flatten: true });
  await client.call("Page.navigate", { url: `${baseUrl}/?legacy=1` }, sessionId);
  const evaluate = (expression) => client.evaluate(sessionId, expression);
  await waitFor(() => evaluate('Boolean(document.querySelector("select[data-task-id]"))'), "真实班表加载");
  return evaluate;
}

function taskAssignments(payload) {
  return payload.assignments.map((assignment) => `${assignment.taskId}:${assignment.memberId}`).sort();
}

async function changeMember(evaluate, value, oldValue, taskId = "pair") {
  const previousCount = requests.length;
  const selector = `select[data-task-id=${JSON.stringify(taskId)}]`;
  await evaluate(`(() => {
    const selects = [...document.querySelectorAll(${JSON.stringify(selector)})];
    const select = ${oldValue === undefined ? "selects[0]" : `selects.find((item) => item.value === ${JSON.stringify(oldValue)})`};
    if (!select) throw new Error("找不到需要修改的人员下拉框");
    select.value = ${JSON.stringify(value)};
    select.dispatchEvent(new Event("change", { bubbles: true }));
  })()`);
  await waitFor(() => requests.length > previousCount, "人员调整发送真实校验请求");
  await waitFor(() => evaluate(`document.querySelectorAll('select[data-task-id="pair"]').length > 0`), "调整后班表");
  // 等待 fetch 的回包及 render，避免在已移除的旧节点上触发下一次操作。
  await evaluate("new Promise((resolveFrame) => requestAnimationFrame(() => requestAnimationFrame(resolveFrame)))");
  return requests.at(-1);
}

test("双人岗位完整显示两名已安排人员", async (t) => {
  const evaluate = await openPage(t);
  const selected = await evaluate("[...document.querySelectorAll('select[data-task-id=\"pair\"]')].map((item) => item.value)");
  assert.deepEqual(selected.sort(), ["member-0", "member-1"], "甲、乙都必须在可编辑班表中显示");
});

test("双人岗位只换甲为丙，校验与保存均保留乙和其他任务", async (t) => {
  const evaluate = await openPage(t);
  const request = await changeMember(evaluate, "member-2");
  const expected = ["other:member-3", "pair:member-1", "pair:member-2"];
  assert.equal(request.path, "/api/validate-manual-assignments");
  assert.deepEqual(taskAssignments(request.payload), expected);
  await evaluate('document.querySelector("#save").click()');
  await waitFor(() => requests.some((item) => item.path === "/api/manual-assignments"), "保存修改请求");
  assert.deepEqual(taskAssignments(requests.at(-1).payload), expected);
});

test("双人岗位可以独立修改第二人", async (t) => {
  const evaluate = await openPage(t);
  const request = await changeMember(evaluate, "member-2", "member-1");
  assert.deepEqual(taskAssignments(request.payload), ["other:member-3", "pair:member-0", "pair:member-2"]);
});

test("修改其他单人任务不影响双人岗位", async (t) => {
  const evaluate = await openPage(t);
  const request = await changeMember(evaluate, "member-2", "member-3", "other");
  assert.deepEqual(taskAssignments(request.payload), ["other:member-2", "pair:member-0", "pair:member-1"]);
});

test("清空一人保留搭档与空位，随后可以补上新人", async (t) => {
  const evaluate = await openPage(t);
  const cleared = await changeMember(evaluate, "");
  assert.deepEqual(taskAssignments(cleared.payload), ["other:member-3", "pair:member-1"]);
  const selected = await evaluate("[...document.querySelectorAll('select[data-task-id=\"pair\"]')].map((item) => item.value)");
  assert.deepEqual(selected.sort(), ["", "member-1"]);
  const filled = await changeMember(evaluate, "member-2", "");
  assert.deepEqual(taskAssignments(filled.payload), ["other:member-3", "pair:member-1", "pair:member-2"]);
});

for (const source of ["manual", "automatic"]) {
  test(`${source} 真实已保存班表不标作合成演示`, async (t) => {
    const evaluate = await openPage(t, source);
    const title = await evaluate('document.querySelector(".topbar p").textContent');
    assert.doesNotMatch(title, /合成演示|演示数据|测试夹具/);
    assert.match(title, /2026-08-02/);
  });
}
