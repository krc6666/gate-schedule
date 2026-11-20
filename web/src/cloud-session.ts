export type LoginRequest = {id: string; status: "pending" | "approved" | "rejected" | "expired" | "cancelled"};
export interface SessionBackend {
  acquireSession(id: string): Promise<void>;
  touchSession(id: string): Promise<void>;
  releaseSession(id: string): Promise<void>;
  requestSession(id: string, request: string): Promise<LoginRequest>;
  pollSession(id: string, request: string | null): Promise<LoginRequest | null>;
  decideRequest(id: string, request: string, allow: boolean): Promise<void>;
  cancelRequest(id: string, request: string): Promise<void>;
}
type Phase = "connecting" | "waiting" | "blocked" | "active" | "paused" | "transferred" | "stopped";
export type SessionState = {phase: Phase; request: LoginRequest | null; message: string; deciding: boolean};

/** Database owns authorization. Timers only refresh its decisions. */
export class CloudSession {
  state: SessionState = {phase: "connecting", request: null, message: "", deciding: false};
  private epoch = 0;
  private polling = false;
  private touching = false;
  private pollTimer: ReturnType<typeof setTimeout> | undefined;
  private heartbeatTimer: ReturnType<typeof setInterval> | undefined;
  constructor(private backend: SessionBackend, readonly id: string, private changed: (state: SessionState) => void) {}
  private update(values: Partial<SessionState>): void { this.state = {...this.state, ...values}; this.changed(this.state); }
  private timers(): void {
    clearTimeout(this.pollTimer);
    if (this.state.phase !== "active") { clearInterval(this.heartbeatTimer); this.heartbeatTimer = undefined; }
    else if (!this.heartbeatTimer) this.heartbeatTimer = setInterval(() => void this.heartbeat(), 30000);
    if (["active", "waiting"].includes(this.state.phase)) this.pollTimer = setTimeout(() => void this.poll(), 3000);
  }
  async connect(): Promise<void> {
    const epoch = ++this.epoch;
    this.update({phase: "connecting", request: null, message: ""}); this.timers();
    try {
      await this.backend.acquireSession(this.id);
      if (epoch !== this.epoch) { await this.backend.releaseSession(this.id); return; }
      this.update({phase: "active"});
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (String(error).includes("SESSION_ACTIVE")) {
        try {
          const request = await this.backend.requestSession(this.id, crypto.randomUUID());
          if (epoch !== this.epoch) { await this.backend.cancelRequest(this.id, request.id); return; }
          this.update({phase: "waiting", request, message: "已申请登录，等待原设备确认。"});
        } catch (requestError) { if (epoch === this.epoch) this.update({phase: "blocked", message: String(requestError)}); }
      } else this.update({phase: "blocked", message: String(error)});
    }
    this.timers();
  }
  async poll(): Promise<void> {
    if (this.polling || this.state.deciding || !["active", "waiting"].includes(this.state.phase)) return;
    this.polling = true; const epoch = this.epoch; const waiting = this.state.phase === "waiting";
    try {
      const request = await this.backend.pollSession(this.id, waiting ? this.state.request!.id : null);
      if (epoch !== this.epoch || this.state.deciding) return;
      if (waiting) {
        if (!request || request.id !== this.state.request?.id) throw Error("登录申请回执不一致，请重新申请。");
        if (request.status === "approved") {
          await this.backend.acquireSession(this.id);
          if (epoch === this.epoch) this.update({phase: "active", request: null, message: ""});
        } else if (request.status !== "pending") this.update({phase: "blocked", request: null, message: request.status === "rejected" ? "原设备已拒绝，本次登录未获允许。" : "登录申请已超时或取消，请重新申请。"});
      } else this.update({request: request?.status === "pending" ? request : null});
    } catch (error) {
      if (epoch === this.epoch) this.update({phase: waiting ? "waiting" : "paused", message: String(error)});
    } finally { this.polling = false; this.timers(); }
  }
  async heartbeat(): Promise<void> {
    if (this.touching || this.state.phase !== "active" || this.state.deciding) return;
    this.touching = true; const epoch = this.epoch;
    try { await this.backend.touchSession(this.id); }
    catch (error) { if (epoch === this.epoch) { this.update({phase: "paused", message: String(error)}); this.timers(); } }
    finally { this.touching = false; }
  }
  pause(error: unknown): void { if (this.state.phase === "active") { this.update({phase: "paused", message: String(error)}); this.timers(); } }
  async resume(): Promise<void> {
    if (this.state.phase !== "paused") return;
    const epoch = this.epoch;
    try { await this.backend.acquireSession(this.id); if (epoch === this.epoch) this.update({phase: "active", message: ""}); }
    catch (error) { if (epoch === this.epoch) this.update({message: String(error)}); }
    this.timers();
  }
  async decide(allow: boolean, save: () => Promise<boolean>): Promise<void> {
    if (this.state.phase !== "active" || !this.state.request || this.state.deciding) return;
    const request = this.state.request; const epoch = ++this.epoch;
    this.update({deciding: true, message: ""}); this.timers();
    try {
      if (allow && !await save()) throw Error("请先完成当前编辑或计算，并保存成功；未交接，当前输入保留。");
      if (epoch !== this.epoch) return;
      await this.backend.decideRequest(this.id, request.id, allow);
      if (epoch === this.epoch) this.update({phase: allow ? "transferred" : "active", request: null, message: allow ? "已允许另一台设备登录，本设备已结束编辑。" : "已拒绝另一台设备登录，你可以继续操作。"});
    } catch (error) { if (epoch === this.epoch) this.update({message: String(error)}); }
    finally { if (epoch === this.epoch) this.update({deciding: false}); this.timers(); }
  }
  async stop(): Promise<void> {
    const request = this.state.phase === "waiting" ? this.state.request : null;
    ++this.epoch; this.update({phase: "stopped", request: null, deciding: false}); this.timers();
    try { if (request) await this.backend.cancelRequest(this.id, request.id); }
    finally { await this.backend.releaseSession(this.id); }
  }
}
