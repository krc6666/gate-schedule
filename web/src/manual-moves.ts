import type { Plan, State } from "./workbench-model";

export type ManualMove = {
  memberId: string; sourceTaskId?: string; sourceCellId?: string;
  targetTaskId?: string; targetCellId?: string; targetMemberId?: string;
};
type Entry = { planId: string; move: ManualMove; done: (saved: boolean) => void };
type Adapter = {
  current(): State;
  save(planId: string, move: ManualMove, revision: number): Promise<State>;
  changed(state: State, pending: number, error?: string): void;
};

/** Display the user's move while the existing backend checks and saves it.
 * This projection grants no eligibility or rule permission and is never stored.
 * The returned server state alone owns issues, statistics and saved revisions.
 */
function displayMove(plan: Plan, move: ManualMove): Plan {
  const draft = plan.draft.map(a => ({ ...a }));
  const cells = { ...plan.cellDrafts };
  const names = Object.fromEntries(Object.entries(plan.memberIds).map(([name, id]) => [id, name]));
  const cellNames = (cell: string) => (cells[cell] ?? "").split(/[/／、,，;；\n]+/).map(n => n.trim()).filter(Boolean);
  const sourceIndex = move.sourceCellId ? cellNames(move.sourceCellId).indexOf(names[move.memberId]) : -1;
  const targetIndex = move.targetCellId && move.targetMemberId ? cellNames(move.targetCellId).indexOf(names[move.targetMemberId]) : -1;
  const remove = (task: string | undefined, cell: string | undefined, member: string) => {
    if (task) {
      const index = draft.findIndex(a => a.taskId === task && a.memberId === member);
      if (index >= 0) draft.splice(index, 1);
    } else if (cell) {
      const remaining = cellNames(cell), index = remaining.indexOf(names[member]);
      if (index >= 0) remaining.splice(index, 1);
      if (remaining.length) cells[cell] = remaining.join(" / "); else delete cells[cell];
    }
  };
  const add = (task: string | undefined, cell: string | undefined, member: string, index = -1) => {
    if (task) draft.push({ taskId: task, memberId: member });
    else if (cell) {
      const values = cellNames(cell);
      values.splice(index >= 0 ? index : values.length, 0, names[member]);
      cells[cell] = values.join(" / ");
    }
  };
  remove(move.sourceTaskId, move.sourceCellId, move.memberId);
  if (move.targetMemberId) {
    remove(move.targetTaskId, move.targetCellId, move.targetMemberId);
    add(move.sourceTaskId, move.sourceCellId, move.targetMemberId, sourceIndex);
  }
  add(move.targetTaskId, move.targetCellId, move.memberId, targetIndex);
  return { ...plan, draft, cellDrafts: cells };
}

export class ManualMoves {
  private entries: Entry[] = [];
  private confirmed: State | null = null;
  private running = false;
  constructor(private adapter: Adapter) {}
  get pending(): boolean { return this.entries.length > 0; }

  add(planId: string, move: ManualMove): Promise<boolean> {
    if (!this.pending) this.confirmed = this.adapter.current();
    const result = new Promise<boolean>(done => this.entries.push({ planId, move: { ...move }, done }));
    this.publish();
    if (!this.running) { this.running = true; void this.save(); }
    return result;
  }

  private publish(error?: string): void {
    let view = this.confirmed!;
    for (const entry of this.entries) {
      view = { ...view, plans: { ...view.plans, [entry.planId]: displayMove(view.plans[entry.planId], entry.move) } };
    }
    this.adapter.changed(view, this.entries.length, error);
  }

  private async save(): Promise<void> {
    try {
      while (this.entries.length) {
        const entry = this.entries[0];
        try {
          this.confirmed = await this.adapter.save(entry.planId, entry.move, this.confirmed!.revision);
          this.entries.shift();
          this.publish();
          entry.done(true);
        } catch (error) {
          // Later gestures may depend on this one. Cancel them together and
          // restore the last confirmed state, preserving earlier saved moves.
          const cancelled = this.entries.splice(0);
          this.publish(error instanceof Error ? error.message : "保存失败");
          cancelled.forEach(item => item.done(false));
        }
      }
    } finally { this.running = false; }
  }
}
