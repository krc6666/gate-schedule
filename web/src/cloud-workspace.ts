/** The sole owner of cloud receipt state. Local commits never acknowledge cloud saves. */
export class CloudWorkspace {
  saving = false;
  savedAt: Date | null = null;
  error = "";
  pending = false;
  private generation = 0;
  private savedGeneration = 0;
  private alive = true;
  private compact = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  constructor(public version: number,
    private readonly snapshot: () => Promise<unknown>,
    private readonly upload: (expected: number, documents: unknown) => Promise<number>,
    private readonly notify: () => void,
    private readonly alertFailure: (message: string) => void) {}
  get dirty(): boolean { return this.pending || this.generation !== this.savedGeneration; }
  get needsLeaveWarning(): boolean { return this.saving || this.dirty; }
  get label(): string {
    if (this.saving) return "正在保存到云端...";
    if (this.error) return "保存失败，点击重试";
    if (this.dirty) return "有修改未上传";
    if (!this.savedAt) return "已读取";
    return this.compact ? "已保存" : "已保存 " + this.savedAt.toLocaleTimeString("zh-CN", {hour12: false});
  }
  changed(): void { if (this.alive) { this.generation++; this.error = ""; this.notify(); } }
  async save(prepare: () => Promise<boolean>): Promise<boolean> {
    if (this.saving || !this.alive) return false;
    this.saving = true; this.error = ""; this.notify();
    try {
      if (!await prepare()) throw new Error("数据校验：请先完成当前表单并检查提示，当前修改保留。");
      if (!this.alive) return false;
      const generation = this.generation;
      const documents = await this.snapshot();
      if (!this.alive) return false;
      const revision = await this.upload(this.version, documents);
      if (!this.alive) return false;
      if (!Number.isSafeInteger(revision) || revision !== this.version + 1) throw new Error("服务回执版本无效，请重新读取核对；当前修改保留。");
      this.version = revision; this.savedGeneration = generation;
      this.savedAt = new Date(); this.compact = false;
      clearTimeout(this.timer);
      this.timer = setTimeout(() => { if (this.alive) { this.compact = true; this.notify(); } }, 5000);
      return true;
    } catch (error) {
      if (this.alive) { this.error = error instanceof Error ? error.message : "网络或服务异常，当前修改保留。"; this.alertFailure(this.error); }
      return false;
    } finally { this.saving = false; if (this.alive) this.notify(); }
  }
  dispose(): void { this.alive = false; clearTimeout(this.timer); }
}
