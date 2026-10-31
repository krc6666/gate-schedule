/** A grip starts pointer dragging; text editing and native name dragging stay available. */
type Target = { ids: string[]; member?: string; cellId?: string };
type Hooks<T> = { source(el: HTMLElement): T | null; drop(source: T, target: Target): void; started(): void };

export class ScheduleDrag<T> {
  private controller = new AbortController();
  private cleanup: (() => void) | null = null;
  constructor(private root: HTMLElement, private hooks: Hooks<T>) {}
  dispose(): void { this.cleanup?.(); this.controller.abort(); this.controller = new AbortController(); }
  bind(scope: HTMLElement = this.root): void {
    const signal = this.controller.signal;
    scope.querySelectorAll<HTMLElement>('[draggable="true"][data-drag-member]').forEach(el => {
      el.addEventListener("pointerdown", event => {
        if (el.getAttribute("draggable") !== "true" || el.matches(":disabled")) return;
        const grip = (event.target as Element).closest("[data-drag-handle]");
        if ((event.pointerType === "mouse" && !grip) || event.button !== 0) return;
        const source = this.hooks.source(el);
        if (!source) return;
        event.preventDefault();
        const x = event.clientX, y = event.clientY;
        let active = false, ghost: HTMLElement | null = null, cell: HTMLElement | null = null;
        let px = x, py = y, frame = 0;
        const events = new AbortController();
        const clearTarget = () => { cell?.classList.remove("is-drop-target"); cell = null; };
        const targetAt = (): Target | null => {
          const hit = document.elementFromPoint(px, py);
          const found = hit?.closest<HTMLElement>("[data-drop-ids]");
          clearTarget();
          if (!found || !this.root.contains(found)) return null;
          cell = found; cell.classList.add("is-drop-target");
          const name = hit?.closest<HTMLElement>("[data-drop-member]");
          return { ids: JSON.parse(name?.dataset.taskIds ?? cell.dataset.dropIds!), member: name?.dataset.dropMember, cellId: cell.dataset.cellId };
        };
        const scroll = () => {
          if (!active) return;
          const sheet = this.root.querySelector<HTMLElement>(".schedule-sheet");
          if (sheet) {
            const box = sheet.getBoundingClientRect();
            if (py >= box.top && py <= box.bottom) {
              if (px > box.right - 40) sheet.scrollLeft += 14;
              else if (px < box.left + 40) sheet.scrollLeft -= 14;
            }
          }
          if (py > innerHeight - 45) window.scrollBy(0, 12);
          else if (py < 100) window.scrollBy(0, -12);
          targetAt(); frame = requestAnimationFrame(scroll);
        };
        const cleanup = () => {
          events.abort(); cancelAnimationFrame(frame); clearTarget(); ghost?.remove();
          el.classList.remove("is-dragging"); document.body.classList.remove("schedule-pointer-dragging");
          if (el.hasPointerCapture(event.pointerId)) el.releasePointerCapture(event.pointerId);
          this.cleanup = null;
        };
        this.cleanup?.(); this.cleanup = cleanup;
        el.setPointerCapture(event.pointerId);
        document.addEventListener("pointermove", move => {
          if (move.pointerId !== event.pointerId) return;
          px = move.clientX; py = move.clientY;
          if (!active && Math.hypot(px - x, py - y) < 6) return;
          move.preventDefault();
          if (!active) {
            active = true; this.hooks.started(); window.getSelection()?.removeAllRanges();
            el.classList.add("is-dragging"); document.body.classList.add("schedule-pointer-dragging");
            ghost = document.createElement("div"); ghost.className = "schedule-drag-preview";
            ghost.textContent = el.textContent; ghost.setAttribute("aria-hidden", "true"); document.body.append(ghost);
            frame = requestAnimationFrame(scroll);
          }
          ghost!.style.transform = `translate(${px + 14}px,${py + 14}px)`; targetAt();
        }, { signal: events.signal, passive: false });
        document.addEventListener("pointerup", up => {
          if (up.pointerId !== event.pointerId) return;
          px = up.clientX; py = up.clientY;
          const target = active ? targetAt() : null;
          if (active) {
            // Prevent the release click from opening the name picker after a drag.
            const block = (click: MouseEvent) => { click.preventDefault(); click.stopImmediatePropagation(); };
            document.addEventListener("click", block, { capture: true, once: true });
            window.setTimeout(() => document.removeEventListener("click", block, true), 0);
          }
          cleanup(); if (target) this.hooks.drop(source, target);
        }, { signal: events.signal });
        document.addEventListener("pointercancel", cleanup, { signal: events.signal, once: true });
        document.addEventListener("keydown", key => { if (key.key === "Escape") cleanup(); }, { signal: events.signal });
        window.addEventListener("blur", cleanup, { signal: events.signal, once: true });
        el.addEventListener("dragstart", native => native.preventDefault(), { signal: events.signal });
      }, { signal });
    });
    scope.querySelectorAll<HTMLElement>("[data-drag-handle]").forEach(grip => grip.addEventListener("keydown", key => {
      if (key.key === "Enter" || key.key === " ") { key.preventDefault(); grip.closest<HTMLElement>("[data-drag-member]")?.click(); }
    }, { signal }));
  }
}
