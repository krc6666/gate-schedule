import type { Day, Plan, State } from "./workbench-model";

type Setting = "configuration" | "balanceSettings" | "teamLeaderSettings" | "halfRestSettings" | "relaxedShiftSettings" | "handoffPreferences" | "dutyRoster";
type DraftSettings = Partial<Pick<State, Setting>>;
type CommandEffect = { followPlan?: boolean; resetDay?: boolean; clearAll?: boolean; saved?: Setting; rulesSaved?: boolean; closeDialog?: boolean; schedule?: boolean };
type EditAdapter = {
  context: () => { state: State; plan: Plan | null };
  execute: (action: string, payload: unknown, planId?: string | null) => Promise<boolean>;
  validate: () => boolean;
  confirmDayChange: () => boolean;
  saveCells: () => Promise<boolean>;
  changed: () => void;
};

// This is the single owner of command effects on pending edits. Display notices
// and scheduling rules stay with their existing owners.
const followPlan = { followPlan: true, resetDay: true, clearAll: true };
const schedule = { ...followPlan, closeDialog: true, schedule: true };
const commandEffects: Record<string, CommandEffect> = {
  "new-plan": followPlan,
  "select-plan": followPlan,
  "import-complete-configuration": schedule,
  "archive-next": schedule,
  "prepare-solve": schedule,
  "phase-plan": { ...followPlan, schedule: true },
  "save-input": { resetDay: true },
  "refresh-people": { resetDay: true },
  "apply-config": { resetDay: true },
  "configure-cell": { resetDay: true, closeDialog: true },
  "cancel-preview": { resetDay: true },
  "save-rules": { resetDay: true, rulesSaved: true },
  "save-config": { saved: "configuration" },
  "save-duty-settings": { saved: "dutyRoster" },
  "update-balance-settings": { saved: "balanceSettings" },
  "save-handoff-preferences": { saved: "handoffPreferences" },
  "save-role": { closeDialog: true },
  "save-roles": { closeDialog: true },
  "save-flight-template": { closeDialog: true },
  "copy-flight-roles": { closeDialog: true },
  "save-person": { closeDialog: true },
  "import-qualifications": { closeDialog: true },
  "move-assignment": { closeDialog: true },
};

/** Pending business edits and the complete save/discard/continue lifecycle.
 * The adapter renders prompts and persists commands; it never resolves a pending
 * continuation itself or maintains a second list of unsaved settings.
 */
export class WorkbenchEdits {
  settings: DraftSettings = {};
  day: Day | null = null;
  dayDirty = false;
  rotation: { enabled: boolean; minutes: number } | null = null;
  readonly cells = new Map<string, string>();
  private pending: ((answer: boolean) => void) | null = null;
  private saving = false;

  constructor(private readonly adapter: EditAdapter) {}

  get hasSettings(): boolean { return Object.keys(this.settings).length > 0; }
  get hasChanges(): boolean { return this.dayDirty || this.hasSettings || this.cells.size > 0; }
  get confirming(): boolean { return this.pending !== null; }

  resetDay(plan: Plan | null): void {
    this.day = plan ? structuredClone(plan.day) : null;
    this.dayDirty = false;
    this.rotation = null;
  }

  discard(plan = this.adapter.context().plan): void {
    this.settings = {};
    this.cells.clear();
    this.resetDay(plan);
  }

  committed(action: string, plan: Plan | null): CommandEffect {
    const effect = commandEffects[action] ?? {};
    if (effect.clearAll) this.discard(plan);
    else {
      if (effect.rulesSaved) {
        for (const key of Object.keys(this.settings) as Setting[]) {
          if (key !== "dutyRoster") delete this.settings[key];
        }
      }
      if (effect.saved) delete this.settings[effect.saved];
      if (effect.resetDay) this.resetDay(this.adapter.context().plan);
    }
    return effect;
  }

  async beforeContinue(autoSaveCells = false): Promise<boolean> {
    if (this.pending || this.saving) return false;
    if (!this.hasChanges) return true;
    if (autoSaveCells && !this.dayDirty && !this.hasSettings) return this.save();
    return new Promise(resolve => { this.pending = resolve; this.adapter.changed(); });
  }

  async resolveLeave(choice: "save" | "discard" | "stay"): Promise<void> {
    if (!this.pending || this.saving) return;
    if (choice === "save" && (!await this.save() || this.hasChanges)) return;
    if (choice === "discard") this.discard();
    const resolve = this.pending;
    this.pending = null;
    this.adapter.changed();
    resolve(choice !== "stay");
  }

  async save(scope: "all" | "rules" | "day" = "all"): Promise<boolean> {
    if (this.saving) return false;
    this.saving = true;
    try {
      const { state, plan } = this.adapter.context();
      const hasRules = Object.keys(this.settings).some(key => key !== "dutyRoster");
      const saveRules = scope === "rules" || scope === "all" && hasRules;
      const saveDay = scope === "day" || this.dayDirty;
      if (saveRules || saveDay) {
        if (saveRules && !this.adapter.validate()) return false;
        if (saveDay && plan?.draft.length && !this.adapter.confirmDayChange()) return false;
        if (saveDay && (!this.day || !plan)) return false;
        const input = this.day && plan ? {
          day: this.day,
          rotationMinutes: this.rotation?.minutes ?? plan.rotationMinutes,
          rotationEnabled: this.rotation?.enabled ?? plan.rotationEnabled,
        } : undefined;
        if (saveRules) {
          const settings = { ...state, ...this.settings };
          if (!await this.adapter.execute("save-rules", {
            configuration: settings.configuration,
            balanceSettings: settings.balanceSettings,
            teamLeaderSettings: settings.teamLeaderSettings,
            halfRestSettings: settings.halfRestSettings,
            relaxedShiftSettings: settings.relaxedShiftSettings,
            preferences: settings.handoffPreferences,
            ...(saveDay ? { input } : {}),
          })) return false;
        } else if (!await this.adapter.execute("save-input", input)) return false;
      }
      if (scope === "all") {
        if (this.settings.dutyRoster && !await this.adapter.execute("save-duty-settings", this.settings.dutyRoster, null)) return false;
        if (this.cells.size && !await this.adapter.saveCells()) return false;
      }
      return true;
    } finally { this.saving = false; }
  }
}
