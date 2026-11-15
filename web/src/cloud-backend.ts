import {createClient} from "@supabase/supabase-js";
import type {Documents} from "./browser-local";

export {cloudConfiguration} from "./cloud-config";

function failure(error: {message?: string; code?: string}): Error {
  if (error.message?.includes("WORKSPACE_CONFLICT")) return new Error("版本冲突：另一页面或电脑已更新云端，已阻止覆盖。请先导出当前资料，再重新登录读取最新版本；不会自动合并。");
  if (error.code === "42501" || error.message?.includes("ADMIN_REQUIRED")) return new Error("权限不足：此账号不是数据库指定的管理员，或会话已失效。当前修改保留。");
  if (error.code === "22023" || error.message?.includes("INVALID_WORKSPACE")) return new Error("数据校验失败：云端拒绝不完整或不支持的工作区，当前修改保留。");
  return new Error("网络或云端服务异常：请检查网络、会话及项目状态后重试，当前修改保留。");
}

export function createCloudBackend(configuration: {url: string; key: string}) {
  const client = createClient(configuration.url, configuration.key, {auth: {persistSession: false, detectSessionInUrl: false}});
  return {
    client,
    async read(): Promise<{revision: number; documents: Documents | null}> {
      const {data, error} = await client.rpc("gate_read_workspace").abortSignal(AbortSignal.timeout(30000));
      if (error) throw failure(error);
      if (!data || !Number.isSafeInteger(data.revision) || data.revision < 0 || !(data.documents === null && data.revision === 0 || data.documents && typeof data.documents === "object" && !Array.isArray(data.documents) && data.revision > 0)) throw new Error("数据校验失败：云端回执损坏，未使用初始数据替代。");
      return data;
    },
    async save(expected: number, documents: unknown): Promise<number> {
      const {data, error} = await client.rpc("gate_save_workspace", {expected_revision: expected, new_documents: documents}).abortSignal(AbortSignal.timeout(30000));
      if (error) throw failure(error);
      if (!data || !Number.isSafeInteger(data.revision)) throw new Error("云端保存回执无效，未确认成功。");
      return data.revision;
    },
  };
}
