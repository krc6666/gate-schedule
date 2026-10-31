import { html, type State, type Plan, type Candidate, type EditPreview } from "./workbench-model";

type Context = { state: State; plan: Plan; disabled: boolean };
type Hooks = {
  context(): Context;
  changed(cellId: string, text: string): void;
  save(cellId: string): Promise<boolean>;
  cancel(cellId: string): void;
  slot(taskId: string, slot: number, memberId: string): Promise<boolean>;
};
type Target = { anchor: HTMLElement; cellId?: string; ids: string[]; taskId?: string; slot: number; input?: HTMLElement; range?: [number, number]; text?: string };

/** Owns the short-lived editor. Every request is tied to the current render and edit. */
export class ScheduleEditor {
  private popup: HTMLDivElement | null = null;
  private target: Target | null = null;
  private generation = 0;
  private request = 0;
  private timer = 0;
  private options: Candidate[] = [];
  private optionsPlanId = "";
  private selected = -1;
  private composing = false;
  private controller = new AbortController();
  constructor(private root: HTMLElement, private hooks: Hooks) {}

  dispose(): void {
    this.close(); this.controller.abort(); this.controller = new AbortController(); this.generation++;
  }
  private close(): void {
    window.clearTimeout(this.timer); this.request++; this.popup?.remove(); this.popup = null;
    this.target?.input?.removeAttribute("aria-controls"); this.target?.input?.setAttribute("aria-expanded", "false");
    this.target = null; this.options = []; this.optionsPlanId = ""; this.selected = -1;
  }
  dismiss(): void { this.close(); }
  bind(): void {
    const { disabled } = this.hooks.context();
    if (disabled) return;
    const signal = this.controller.signal;
    this.root.querySelectorAll<HTMLElement>("[data-cell-text]").forEach(el => {
      const cellId = el.dataset.cellText!;
      el.addEventListener("compositionstart", () => { this.composing = true; }, { signal });
      el.addEventListener("compositionend", () => { this.composing = false; this.typing(el); }, { signal });
      el.addEventListener("input", () => {
        this.hooks.changed(cellId, el.textContent ?? "");
        if (!this.composing) this.typing(el);
      }, { signal });
      el.addEventListener("keydown", event => {
        if (event.isComposing || this.composing) return;
        if (this.keyboard(event)) return;
        if (event.key === "Enter") { event.preventDefault(); this.close(); void this.hooks.save(cellId); }
        if (event.key === "Escape") { event.preventDefault(); this.close(); this.hooks.cancel(cellId); }
      }, { signal });
      el.addEventListener("blur", () => {
        window.setTimeout(() => {
          if (!el.isConnected || this.popup?.contains(document.activeElement)) return;
          this.close(); void this.hooks.save(cellId);
        }, 0);
      }, { signal });
      el.addEventListener("paste", event => {
        event.preventDefault(); document.execCommand("insertText", false, (event.clipboardData?.getData("text/plain") ?? "").replace(/\r?\n/g, " / "));
      }, { signal });
    });
    this.root.querySelectorAll<HTMLElement>("[data-pick-cell],.schedule-name").forEach(el => el.addEventListener("click", event => {
      if (this.hooks.context().disabled || this.root.querySelector(".palette-person.selected")) return;
      event.preventDefault(); event.stopPropagation();
      const cell = el.closest<HTMLElement>("[data-drop-ids]")!;
      const ids: string[] = JSON.parse(el.dataset.taskIds ?? cell.dataset.dropIds!);
      const t: Target = { anchor: el, cellId: cell.dataset.cellId, ids, slot: 0 };
      if (ids.length === 1) {
        t.taskId = ids[0];
        const assigned = this.hooks.context().plan.draft.filter(a => a.taskId === ids[0]);
        t.slot = el.dataset.dragMember ? assigned.findIndex(a => a.memberId === el.dataset.dragMember) : assigned.length;
      }
      this.open(t);
    }, { signal }));
    document.addEventListener("pointerdown", event => {
      if (this.popup && !this.popup.contains(event.target as Node) && !this.target?.anchor.contains(event.target as Node)) this.close();
    }, { signal });
    window.addEventListener("resize", () => this.position(), { signal });
    window.addEventListener("scroll", () => this.position(), { signal, capture: true });
  }
  private typing(el: HTMLElement): void {
    const cell = el.closest<HTMLElement>("[data-drop-ids]")!;
    const text = el.textContent ?? "";
    let offset = text.length;
    const selection = window.getSelection();
    if (selection?.rangeCount && el.contains(selection.anchorNode)) {
      const range = selection.getRangeAt(0).cloneRange(); range.selectNodeContents(el); range.setEnd(selection.anchorNode!, selection.anchorOffset); offset = range.toString().length;
    }
    const separators = /[\/／、,，;；\n]/;
    let start = offset, end = offset;
    while (start && !separators.test(text[start - 1])) start--;
    while (end < text.length && !separators.test(text[end])) end++;
    const ids: string[] = JSON.parse(cell.dataset.dropIds!);
    const t: Target = { anchor: el, input: el, cellId: el.dataset.cellText!, ids, slot: text.slice(0, start).split(separators).length - 1, range: [start, end], text };
    if (ids.length === 1) t.taskId = ids[0];
    this.open(t, text.slice(start, end).trim());
  }
  private open(t: Target, query = ""): void {
    this.close(); this.target = t;
    this.popup = document.createElement("div"); this.popup.id = "schedule-candidate-menu"; this.popup.className = "schedule-candidate-menu";
    this.popup.setAttribute("role", "region"); this.popup.setAttribute("aria-label", "岗位选人");
    document.body.append(this.popup); this.position();
    if (t.input) { t.input.setAttribute("aria-controls", this.popup.id); t.input.setAttribute("aria-expanded", "true"); }
    const { plan } = this.hooks.context();
    if (!t.input && t.ids.length) {
      const choices = t.ids.flatMap(id => {
        const task = plan.schedule!.tasks.find(task => task.id === id)!;
        const assigned = plan.draft.filter(a => a.taskId === id);
        return Array.from({ length: Math.max(task.requiredCount, assigned.length) }, (_, slot) => ({ id, slot, label: `${task.timingLabel ?? task.roleId} · ${assigned[slot] ? `替换${plan.schedule!.members.find(m => m.id === assigned[slot].memberId)?.name}` : "待安排"}` }));
      });
      if (!t.taskId || !choices.some(c => c.id === t.taskId && c.slot === t.slot)) {
        this.popup.innerHTML = `<strong>选择要安排的位置</strong>${choices.map((c, i) => `<button type="button" data-target-choice="${i}">${html(c.label)}</button>`).join("")}`;
        this.popup.querySelectorAll<HTMLButtonElement>("[data-target-choice]").forEach(b => b.onclick = () => { const c = choices[Number(b.dataset.targetChoice)]; this.open({ ...t, taskId: c.id, slot: c.slot }); });
        return;
      }
    }
    this.popup.innerHTML = `<div class="candidate-heading"><strong>${t.input ? "姓名匹配" : "选择人员"}</strong><button type="button" data-close-picker aria-label="关闭选人">×</button></div>${t.input ? "" : '<input class="candidate-search" aria-label="搜索人员" placeholder="输入姓名筛选" autocomplete="off" />'}<div class="candidate-notice" aria-live="polite">正在检查人选…</div><div class="candidate-list" role="listbox" aria-label="匹配人员"></div><div class="candidate-preview" aria-live="polite"></div>${!t.input && t.taskId ? '<button type="button" data-clear-slot>清除此人</button>' : ""}`;
    this.popup.querySelector<HTMLButtonElement>("[data-close-picker]")!.onclick = () => this.close();
    const search = this.popup.querySelector<HTMLInputElement>(".candidate-search");
    if (search) {
      search.oninput = () => { this.showOptions(search.value); };
      search.onkeydown = e => { if (e.isComposing) return; if (e.key === "Escape") { this.close(); t.anchor.focus(); return; } this.keyboard(e); };
      search.focus();
    }
    this.popup.querySelector<HTMLButtonElement>("[data-clear-slot]")?.addEventListener("click", () => { this.close(); void this.hooks.slot(t.taskId!, t.slot, ""); });
    // Preserve the active text selection while choosing a suggestion.
    this.popup.addEventListener("pointerdown", e => { if (!(e.target instanceof HTMLInputElement)) e.preventDefault(); });
    const generation = this.generation, request = ++this.request;
    const context = this.hooks.context();
    const expectedRevision = context.state.revision, expectedPlanId = context.plan.id;
    const active = () => generation === this.generation && request === this.request && this.target === t && !!this.popup?.isConnected && t.anchor.isConnected && this.root.contains(t.anchor);
    const current = () => active() && this.hooks.context().state.revision === expectedRevision && this.hooks.context().plan.id === expectedPlanId;
    const failed = (message: string) => {
      this.options = []; this.optionsPlanId = ""; this.selected = -1;
      this.popup!.querySelector(".candidate-list")!.innerHTML = "";
      this.popup!.querySelector(".candidate-preview")!.innerHTML = "";
      this.popup!.querySelector(".candidate-notice")!.textContent = message;
    };
    const ensureCurrent = () => {
      if (current()) return true;
      if (active()) failed("班表已改变，请重新读取后再选人");
      return false;
    };
    const load = async () => {
      try {
        const endpoint = t.taskId ? "candidates" : "cell-candidates", targetId = t.taskId ?? t.cellId;
        if (!targetId) throw Error("无法识别岗位，请重新打开班表后重试");
        const response = await fetch(`/api/workspace/${endpoint}/${encodeURIComponent(expectedPlanId)}/${encodeURIComponent(targetId)}?slot=${t.slot}`, { signal: this.controller.signal });
        if (!response.ok) throw Error("候选查询失败，请重试");
        const items: Candidate[] = await response.json();
        if (!ensureCurrent()) return;
        if (!Array.isArray(items) || items.some(candidate => candidate.revision !== expectedRevision)) throw Error("班表已改变，请重新读取后再选人");
        this.options = items; this.optionsPlanId = expectedPlanId; this.showOptions(search?.value ?? query);
        if (t.input && t.cellId) {
          const response = await fetch(`/api/workspace/preview-edit/${encodeURIComponent(expectedPlanId)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cellId: t.cellId, text: t.text, expectedRevision }), signal: this.controller.signal });
          if (!response.ok) throw Error("检查未完成，请保存时重试");
          const preview: EditPreview = await response.json();
          if (!ensureCurrent()) return;
          if (preview.revision !== expectedRevision) throw Error("班表已改变，请重新读取后再选人");
          const issues = preview.issues.filter(i => i.code === "edit" || i.taskId === t.cellId || t.ids.includes(i.taskId ?? "") || i.relatedTaskIds?.some(id => t.ids.includes(id)));
          this.popup!.querySelector(".candidate-preview")!.innerHTML = issues.length ? `<span class="text-danger">${issues.map(i => html(i.message)).join("<br>")}</span>` : "当前输入未发现本格冲突；回车保存";
          if (preview.suggestions.length) this.popup!.querySelector(".candidate-preview")!.innerHTML += `<p>可能是：${preview.suggestions.map(html).join("、")}，请确认姓名。</p>`;
        }
      } catch (error) {
        if (!ensureCurrent() || (error instanceof DOMException && error.name === "AbortError")) return;
        failed(error instanceof Error ? error.message : String(error));
      }
    };
    this.timer = window.setTimeout(() => void load(), t.input ? 160 : 0);
  }
  private showOptions(query: string): void {
    if (!this.popup) return;
    const matches = this.options.filter(c => c.name.includes(query.trim()));
    this.selected = -1;
    this.popup.querySelector(".candidate-notice")!.textContent = matches.length ? `${matches.filter(c => c.available).length} 人符合条件 · 有问题也可手工填入并报警` : "没有匹配人员。请核对姓名、当天人员与岗位资料。";
    this.popup.querySelector(".candidate-list")!.innerHTML = matches.map(c => `<button type="button" role="option" aria-selected="false" data-candidate-id="${html(c.id)}" ${c.available || c.manualAllowed ? "" : 'disabled aria-disabled="true"'}><strong>${html(c.name)}</strong><small class="${!c.available && c.manualAllowed ? 'text-danger' : ''}">${[!c.available && c.manualAllowed ? '填入后报警' : c.label, ...c.reasons].filter(Boolean).map(html).join("；")}</small></button>`).join("");
    this.popup.querySelectorAll<HTMLButtonElement>("[data-candidate-id]:not(:disabled)").forEach(b => b.onclick = () => void this.choose(b.dataset.candidateId!));
    this.position();
  }
  private keyboard(event: KeyboardEvent): boolean {
    if (!this.popup) return false;
    const buttons = [...this.popup.querySelectorAll<HTMLButtonElement>("[data-candidate-id]:not(:disabled)")];
    if (["ArrowDown", "ArrowUp"].includes(event.key) && buttons.length) {
      event.preventDefault(); this.selected = this.selected < 0 ? event.key === "ArrowDown" ? 0 : buttons.length - 1 : (this.selected + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons.forEach((b, i) => b.setAttribute("aria-selected", String(i === this.selected))); buttons[this.selected].scrollIntoView({ block: "nearest" }); return true;
    }
    if (event.key === "Enter" && this.selected >= 0 && buttons[this.selected]) { event.preventDefault(); void this.choose(buttons[this.selected].dataset.candidateId!); return true; }
    if (event.key === "Enter" && !this.target?.input) {
      const query = this.popup.querySelector<HTMLInputElement>(".candidate-search")?.value.trim();
      const exact = this.options.find(c => c.name === query && (c.available || c.manualAllowed));
      if (exact) { event.preventDefault(); void this.choose(exact.id); return true; }
    }
    return false;
  }
  private async choose(id: string): Promise<void> {
    const t = this.target, candidate = this.options.find(c => c.id === id);
    const context = this.hooks.context();
    if (!t || !candidate || !(candidate.available || candidate.manualAllowed) || context.disabled || !t.anchor.isConnected) return;
    if (candidate.revision !== context.state.revision || this.optionsPlanId !== context.plan.id) { this.close(); return; }
    this.close();
    if (t.input && t.range && t.cellId) {
      const text = (t.text ?? "").slice(0, t.range[0]) + candidate.name + (t.text ?? "").slice(t.range[1]);
      t.input.textContent = text; this.hooks.changed(t.cellId, text); await this.hooks.save(t.cellId);
    } else if (t.taskId) await this.hooks.slot(t.taskId, t.slot, id);
    else if (t.cellId) { this.hooks.changed(t.cellId, candidate.name); await this.hooks.save(t.cellId); }
  }
  private position(): void {
    if (!this.popup || !this.target?.anchor.isConnected) return;
    const box = this.target.anchor.getBoundingClientRect(), width = Math.min(360, innerWidth - 20);
    this.popup.style.width = `${width}px`;
    this.popup.style.left = `${Math.max(10, Math.min(box.left, innerWidth - width - 10))}px`;
    this.popup.style.top = `${Math.max(10, Math.min(box.bottom + 4, innerHeight - Math.min(380, this.popup.scrollHeight || 380) - 10))}px`;
  }
}
