import {createCloudBackend} from "./cloud-backend";
import {installLocalRuntime, readLegacyDocuments, type Documents, type BrowserRuntime} from "./browser-local";
import {CloudWorkspace} from "./cloud-workspace";
import {cloudBridge} from "./cloud-bridge";
import {CloudSession, type SessionState} from "./cloud-session";
import "./cloud.css";

export async function startCloud(configuration: {url: string; key: string}): Promise<void> {
  document.documentElement.dataset.gateCloud = "true";
  const root = document.querySelector<HTMLDivElement>("#app")!;
  const sessionId = crypto.randomUUID();
  const backend = createCloudBackend(configuration, sessionId);
  let runtime: BrowserRuntime | null = null;
  let sessionUser = "";
  let ended = false;
  let opening = false;
  const toolbar = document.createElement("div"); toolbar.id = "cloud-toolbar";
  const sessionNotice = document.createElement("div"); sessionNotice.className = "cloud-session-notice";
  const session = new CloudSession(backend, sessionId, state => {
    if (ended) return;
    if (state.phase === "transferred") { end(state.message); return; }
    if (state.phase === "stopped") return;
    if (runtime) { renderSession(state); refresh(); return; }
    if (state.phase === "active") {
      if (!opening) { opening = true; void load(); }
      else if (state.request) { root.before(toolbar); toolbar.append(sessionNotice); renderSession(state); }
      return;
    }
    if (state.phase === "connecting") return;
    root.replaceChildren();
    const main = document.createElement("main"); main.className = "cloud-login";
    const message = document.createElement("p"); message.setAttribute("role", "status"); message.textContent = state.message;
    main.append(message);
    if (state.phase === "blocked") { const retry = document.createElement("button"); retry.textContent = "重新申请登录"; retry.onclick = () => void session.connect(); main.append(retry); }
    if (state.phase === "paused") { opening = false; const retry = document.createElement("button"); retry.textContent = "重新验证使用权"; retry.onclick = () => void session.resume(); main.append(retry); }
    const cancel = document.createElement("button"); cancel.textContent = state.phase === "waiting" ? "取消登录申请" : "退出登录";
    cancel.onclick = async () => { await session.stop().catch(() => {}); await backend.client.auth.signOut({scope: "local"}); end(); }; main.append(cancel); root.append(main);
  });
  const renderSession = (state: SessionState) => {
    const frozen = state.phase !== "active" || state.deciding;
    root.inert = frozen;
    // A modal escapes ancestor inertness and blocks toolbar clicks. Keep the
    // session controls in that modal, while freezing only its business fields.
    const modal = root.querySelector<HTMLDialogElement>("dialog[open]");
    (modal ?? toolbar).append(sessionNotice);
    if (modal) for (const child of modal.children) if (child instanceof HTMLElement && child !== sessionNotice) child.inert = frozen;
    sessionNotice.replaceChildren();
    const message = document.createElement("span"); message.textContent = state.phase === "paused" ? "编辑已暂停，当前输入保留。" + state.message : state.message;
    sessionNotice.append(message);
    if (state.request?.status === "pending") {
      const text = document.createElement("span"); text.textContent = "另一台设备请求登录。"; sessionNotice.append(text);
      for (const allow of [false, true]) {
        const button = document.createElement("button"); button.className = "btn btn-outline-secondary"; button.id = allow ? "cloud-allow-login" : "cloud-deny-login";
        button.textContent = allow ? "保存并允许交接" : "拒绝登录"; button.disabled = state.phase !== "active" || state.deciding;
        button.onclick = () => void session.decide(allow, async () => {
          if (!cloudBridge.workspace || !cloudBridge.canTransfer()) return false;
          const cloud = cloudBridge.workspace!; cloud.pending = cloudBridge.hasEdits();
          if (cloud.dirty && !await cloud.save(cloudBridge.prepare)) return false;
          cloud.pending = cloudBridge.hasEdits();
          return !cloud.dirty && cloudBridge.canTransfer();
        }); sessionNotice.append(button);
      }
    }
    if (state.phase === "paused") { const resume = document.createElement("button"); resume.id = "cloud-resume-session"; resume.textContent = "重新验证使用权"; resume.onclick = () => void session.resume(); sessionNotice.append(resume); }
  };
  const end = (message = "会话已结束，请重新登录。") => {
    if (ended) return;
    ended = true; cloudBridge.active = false;
    cloudBridge.workspace?.dispose(); runtime?.dispose(); toolbar.remove(); sessionNotice.remove(); root.inert = false;
    void session.stop().catch(() => {});
    root.textContent = message;
    const button = document.createElement("button"); button.textContent = "重新登录"; button.onclick = () => location.reload(); root.append(button);
  };
  const refresh = () => {
    const cloud = cloudBridge.workspace;
    if (!cloud || ended) return;
    if (runtime) renderSession(session.state);
    cloud.pending = cloudBridge.hasEdits();
    toolbar.querySelector<HTMLButtonElement>("#cloud-save")!.disabled = cloud.saving || session.state.phase !== "active" || session.state.deciding;
    const status = toolbar.querySelector<HTMLButtonElement>("#cloud-status")!;
    status.textContent = (cloud.error ? "ⓘ " : cloud.dirty ? "○ " : "✓ ") + cloud.label;
    status.title = cloud.error || cloud.savedAt?.toLocaleString("zh-CN", {hour12: false}) || "本次登录尚未保存到云端";
    status.disabled = !cloud.error;
  };
  cloudBridge.refresh = refresh;
  const open = async (revision: number, documents: Documents | null) => {
    root.textContent = "正在打开工作区…";
    const cloud = new CloudWorkspace(revision, async () => runtime!.snapshot(), async (expected, docs) => {
      if (session.state.phase !== "active") throw new Error("编辑已暂停，请先验证使用权。");
      try { return await backend.save(expected, docs); } catch (error) { if (String(error).includes("SESSION_EXPIRED")) session.pause(error); throw error; }
    }, refresh, message => alert("保存到云端失败\n\n" + message));
    cloudBridge.workspace = cloud;
    runtime = await installLocalRuntime({documents, changed: () => cloud.changed()}, message => { root.textContent = message; });
    if (ended) { runtime.dispose(); return; }
    toolbar.innerHTML = '<div class="cloud-toolbar-inner"><button id="cloud-save" class="btn btn-primary">保存到云端</button><button id="cloud-status" class="cloud-status" aria-live="polite"></button><details class="cloud-more"><summary aria-label="更多操作（…）">…</summary><div><button id="cloud-reload">重新读取云端</button><button id="cloud-logout">退出登录</button></div></details></div>';
    root.before(toolbar);
    toolbar.append(sessionNotice); renderSession(session.state);
    const save = () => { if (session.state.phase === "active" && !session.state.deciding) void cloud.save(cloudBridge.prepare); };
    toolbar.querySelector<HTMLButtonElement>("#cloud-save")!.onclick = save;
    toolbar.querySelector<HTMLButtonElement>("#cloud-status")!.onclick = save;
    const mayExit = () => { cloud.pending = cloudBridge.hasEdits(); return !cloud.needsLeaveWarning || confirm(cloud.saving ? "正在保存到云端，离开可能无法确认结果。确定离开？" : "有修改尚未保存到云端，离开将丢失当前修改。确定离开？"); };
    toolbar.querySelector<HTMLButtonElement>("#cloud-logout")!.onclick = async () => {
      if (!mayExit()) return;
      const {error} = await backend.client.auth.signOut({scope: "local"});
      if (error) { alert("退出失败，请重试；当前编辑保留。"); return; }
      end();
    };
    toolbar.querySelector<HTMLButtonElement>("#cloud-reload")!.onclick = () => { if (mayExit()) { end(); location.reload(); } };
    document.documentElement.dataset.gateLocal = "true";
    await import("./workbench"); refresh();
  };
  const chooseInitial = async () => {
    root.innerHTML = '<main class="cloud-login"><h1>云端尚无工作区</h1><p>请选择首次使用的资料，点击“保存到云端”后才上传。</p><button id="cloud-migrate" class="btn btn-primary">迁入此浏览器原有资料</button><button id="cloud-empty" class="btn btn-outline-secondary">新建空白工作区</button><p>桌面版资料可在进入后通过“导入数据”迁入。</p><p id="cloud-initial-error" role="alert"></p></main>';
    root.querySelector<HTMLButtonElement>("#cloud-migrate")!.onclick = async () => {
      try {
        const legacy = await readLegacyDocuments();
        if (!legacy["group-workspaces-v1"]) throw new Error("此浏览器没有可迁入的完整A/B资料。原资料保留，请改用完整配置文件导入。");
        if (!confirm("将此浏览器原A/B资料作为首次云端工作区，原本机资料保留。继续？")) return;
        await open(0, {"group-workspaces-v1": legacy["group-workspaces-v1"]});
        cloudBridge.workspace!.changed();
      } catch (error) { showError(error); }
    };
    root.querySelector<HTMLButtonElement>("#cloud-empty")!.onclick = async () => {
      if (!confirm("新建空白工作区，原本机资料保留。之后仍需显式保存到云端。继续？")) return;
      try { await open(0, null); } catch (error) { showError(error); }
    };
  };
  const showError = (error: unknown) => {
    cloudBridge.workspace?.dispose(); runtime?.dispose(); runtime = null; toolbar.remove();
    root.replaceChildren();
    const message = document.createElement("p"); message.setAttribute("role", "alert"); message.textContent = String(error);
    const retry = document.createElement("button"); retry.textContent = "重新读取"; retry.onclick = () => void load();
    const logout = document.createElement("button"); logout.textContent = "退出登录"; logout.onclick = () => { void backend.client.auth.signOut({scope: "local"}).then(() => end()); };
    root.append(message, retry, logout);
  };
  const load = async () => {
    try {
      root.textContent = "正在读取云端…";
      const result = await backend.read();
      if (ended || session.state.phase !== "active") return;
      if (result.documents === null) await chooseInitial(); else await open(result.revision, result.documents);
    } catch (error) { if (!ended) showError(error); }
  };
  backend.client.auth.onAuthStateChange((_event, session) => {
    if (sessionUser && (!session || session.user.id !== sessionUser)) end();
  });
  window.addEventListener("pagehide", () => { void session.stop().catch(() => {}); cloudBridge.workspace?.dispose(); runtime?.dispose(); cloudBridge.active = false; });
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  root.innerHTML = '<main class="cloud-login"><h1>登机口排班</h1><form id="cloud-login-form"><label>管理员邮箱<input id="cloud-email" type="email" autocomplete="username" required /></label><label>密码<input id="cloud-password" type="password" autocomplete="current-password" required /></label><button class="btn btn-primary" type="submit">登录</button><p id="cloud-login-error" role="alert"></p></form></main>';
  root.querySelector<HTMLFormElement>("#cloud-login-form")!.onsubmit = async event => {
    event.preventDefault();
    const button = root.querySelector<HTMLButtonElement>("button")!; button.disabled = true;
    const password = root.querySelector<HTMLInputElement>("#cloud-password")!;
    try {
      const {data, error} = await backend.client.auth.signInWithPassword({email: root.querySelector<HTMLInputElement>("#cloud-email")!.value.trim(), password: password.value});
      if (error || !data.user) throw new Error("登录失败，请检查邮箱、密码或网络。");
      password.value = ""; sessionUser = data.user.id; await session.connect();
    } catch (error) { root.querySelector<HTMLElement>("#cloud-login-error")!.textContent = String(error); }
    finally { button.disabled = false; }
  };
}
