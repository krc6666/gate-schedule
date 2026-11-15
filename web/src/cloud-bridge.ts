import type {CloudWorkspace} from "./cloud-workspace";

/** Workbench retains sole ownership of pending form edits and their validation. */
export const cloudBridge: {
  workspace: CloudWorkspace | null;
  active: boolean;
  prepare: () => Promise<boolean>;
  hasEdits: () => boolean;
  refresh: () => void;
} = {workspace: null, active: true, prepare: async () => false, hasEdits: () => false, refresh: () => {}};
