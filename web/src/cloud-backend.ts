import {createClient} from "@supabase/supabase-js";
import type {Documents} from "./browser-local";
import type {LoginRequest} from "./cloud-session";

export {cloudConfiguration} from "./cloud-config";

function failure(error: {message?: string; code?: string}): Error {
  if (error.code === "PGRST202") return new Error("云端尚未安装登录审批更新，请先由管理员完成云端更新后重试。现有资料未改动。");
  if (error.message?.includes("SESSION_ACTIVE")) return new Error("SESSION_ACTIVE：此账号正在另一台设备使用，需原设备同意。");
  if (error.message?.includes("SESSION_REQUEST_BUSY")) return new Error("已有另一台设备申请登录，请稍后重新申请。");
  if (error.message?.includes("SESSION_REQUEST_")) return new Error("登录申请已结束或无效，未交接，请重新申请。");
  if (error.message?.includes("SESSION_")) return new Error("SESSION_EXPIRED：使用权已失效，当前输入保留，请重新验证。");
  if (error.message?.includes("WORKSPACE_CONFLICT")) return new Error("版本冲突：另一页面或电脑已更新云端，已阻止覆盖。请先导出当前资料，再重新登录读取最新版本；不会自动合并。");
  if (error.code === "42501" || error.message?.includes("ADMIN_REQUIRED")) return new Error("权限不足：此账号不是数据库指定的管理员，或会话已失效。当前修改保留。");
  if (error.code === "22023" || error.message?.includes("INVALID_WORKSPACE")) return new Error("数据校验失败：云端拒绝不完整或不支持的工作区，当前修改保留。");
  return new Error("网络或云端服务异常：请检查网络、会话及项目状态后重试，当前修改保留。");
}

export function createCloudBackend(configuration: {url: string; key: string}, sessionId: string) {
  const client = createClient(configuration.url, configuration.key, {auth: {persistSession: false, detectSessionInUrl: false}});
  const rpc = async (name: string, args: Record<string, unknown>) => {
    const {data, error} = await client.rpc(name, args).abortSignal(AbortSignal.timeout(30000));
    if (error) throw failure(error);
    return data;
  };
  const request = (data: unknown): LoginRequest => {
    const value = data as LoginRequest | null;
    if (!value || typeof value.id !== "string" || value.id.length < 16 || !["pending", "approved", "rejected", "expired", "cancelled"].includes(value.status)) throw new Error("登录申请回执损坏，未授予使用权。");
    return {id: value.id, status: value.status};
  };
  return {
    client,
    async acquireSession(id: string) { await rpc("gate_acquire_session", {p_session: id}); },
    async touchSession(id: string) { await rpc("gate_touch_session", {p_session: id}); },
    async releaseSession(id: string) { await rpc("gate_release_session", {p_session: id}); },
    async requestSession(id: string, requestId: string) { return request(await rpc("gate_request_login", {p_session: id, p_request: requestId})); },
    async pollSession(id: string, requestId: string | null) { const value = await rpc("gate_poll_login", {p_session: id, p_request: requestId}); return value === null ? null : request(value); },
    async decideRequest(id: string, requestId: string, allow: boolean) { await rpc("gate_decide_login", {p_session: id, p_request: requestId, p_allow: allow}); },
    async cancelRequest(id: string, requestId: string) { await rpc("gate_cancel_login", {p_session: id, p_request: requestId}); },
    async read(): Promise<{revision: number; documents: Documents | null}> {
      const data = await rpc("gate_read_workspace_session", {p_session: sessionId});
      if (!data || !Number.isSafeInteger(data.revision) || data.revision < 0 || !(data.documents === null && data.revision === 0 || data.documents && typeof data.documents === "object" && !Array.isArray(data.documents) && data.revision > 0)) throw new Error("数据校验失败：云端回执损坏，未使用初始数据替代。");
      return data;
    },
    async save(expected: number, documents: unknown): Promise<number> {
      const data = await rpc("gate_save_workspace_session", {p_session: sessionId, expected_revision: expected, new_documents: documents});
      if (!data || !Number.isSafeInteger(data.revision)) throw new Error("云端保存回执无效，未确认成功。");
      return data.revision;
    },
  };
}
