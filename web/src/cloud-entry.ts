import {createCloudBackend} from "./cloud-backend";
import {installLocalRuntime, readLegacyDocuments, type Documents, type BrowserRuntime} from "./browser-local";
import {CloudWorkspace} from "./cloud-workspace";
import {cloudBridge} from "./cloud-bridge";
import "./cloud.css";

export async function startCloud(configuration: {url: string; key: string}): Promise<void> {
  document.documentElement.dataset.gateCloud = "true";
  const root = document.querySelector<HTMLDivElement>("#app")!;
  const backend = createCloudBackend(configuration);
  let runtime: BrowserRuntime | null = null;
  let sessionUser = "";
  let ended = false;
  const toolbar = document.createElement("div"); toolbar.id = "cloud-toolbar";
  const end = () => {
    if (ended) return;
    ended = true; cloudBridge.active = false;
    cloudBridge.workspace?.dispose(); runtime?.dispose(); toolbar.remove();
    root.textContent = "会话已结束，请重新登录。";
    const button = document.createElement("button"); button.textContent = "重新登录"; button.onclick = () => location.reload(); root.append(button);
  };
  const refresh = () => {
    const cloud = cloudBridge.workspace;
    if (!cloud || ended) return;
    cloud.pending = cloudBridge.hasEdits();
    toolbar.querySelector<HTMLButtonElement>("#cloud-save")!.disabled = cloud.saving;
    const status = toolbar.querySelector<HTMLButtonElement>("#cloud-status")!;
    status.textContent = (cloud.error ? "ⓘ " : cloud.dirty ? "○ " : "✓ ") + cloud.label;
    status.title = cloud.error || cloud.savedAt?.toLocaleString("zh-CN", {hour12: false}) || "本次登录尚未保存到云端";
    status.disabled = !cloud.error;
  };
  cloudBridge.refresh = refresh;
  const open = async (revision: number, documents: Documents | null) => {
    root.textContent = "正在打开工作区…";
    const cloud = new CloudWorkspace(revision, async () => runtime!.snapshot(), (expected, docs) => backend.save(expected, docs), refresh, message => alert("保存到云端失败\n\n" + message));
    cloudBridge.workspace = cloud;
    runtime = await installLocalRuntime({documents, changed: () => cloud.changed()});
    if (ended) { runtime.dispose(); return; }
    toolbar.innerHTML = '<div class="cloud-toolbar-inner"><button id="cloud-save" class="btn btn-primary">保存到云端</button><button id="cloud-status" class="cloud-status" aria-live="polite"></button><details class="cloud-more"><summary aria-label="更多操作（…）">…</summary><div><button id="cloud-reload">重新读取云端</button><button id="cloud-logout">退出登录</button></div></details></div>';
    root.before(toolbar);
    toolbar.querySelector<HTMLButtonElement>("#cloud-save")!.onclick = () => void cloud.save(cloudBridge.prepare);
    toolbar.querySelector<HTMLButtonElement>("#cloud-status")!.onclick = () => void cloud.save(cloudBridge.prepare);
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
    const logout = document.createElement("button"); logout.textContent = "退出登录"; logout.onclick = () => { void backend.client.auth.signOut({scope: "local"}).then(end); };
    root.append(message, retry, logout);
  };
  const load = async () => {
    try {
      root.textContent = "正在读取云端…";
      const result = await backend.read();
      if (ended) return;
      if (result.documents === null) await chooseInitial(); else await open(result.revision, result.documents);
    } catch (error) { if (!ended) showError(error); }
  };
  backend.client.auth.onAuthStateChange((_event, session) => {
    if (sessionUser && (!session || session.user.id !== sessionUser)) end();
  });
  window.addEventListener("pagehide", () => { cloudBridge.workspace?.dispose(); runtime?.dispose(); cloudBridge.active = false; });
  window.addEventListener("pageshow", event => { if (event.persisted) location.reload(); });
  root.innerHTML = '<main class="cloud-login"><h1>登机口排班</h1><form id="cloud-login-form"><label>管理员邮箱<input id="cloud-email" type="email" autocomplete="username" required /></label><label>密码<input id="cloud-password" type="password" autocomplete="current-password" required /></label><button class="btn btn-primary" type="submit">登录</button><p id="cloud-login-error" role="alert"></p></form></main>';
  root.querySelector<HTMLFormElement>("#cloud-login-form")!.onsubmit = async event => {
    event.preventDefault();
    const button = root.querySelector<HTMLButtonElement>("button")!; button.disabled = true;
    const password = root.querySelector<HTMLInputElement>("#cloud-password")!;
    try {
      const {data, error} = await backend.client.auth.signInWithPassword({email: root.querySelector<HTMLInputElement>("#cloud-email")!.value.trim(), password: password.value});
      if (error || !data.user) throw new Error("登录失败，请检查邮箱、密码或网络。");
      password.value = ""; sessionUser = data.user.id; await load();
    } catch (error) { root.querySelector<HTMLElement>("#cloud-login-error")!.textContent = String(error); }
    finally { button.disabled = false; }
  };
}
