/**
 * Centralized mobile UI state with subscription support.
 *
 * Views read from `state` and call `patchState(partial)` to update.
 * Shell (app.js) subscribes to re-render status bar, badge, tab bar.
 * Active view subscribes to re-render its own content.
 */

export const state = {
  authEnabled: false,
  authenticated: true,
  needsLogin: false,
  activeTab: "recommend",
  online: false,
  degraded: false,
  degradedReason: "",
  runtimeStatus: null,
  runtimeEvent: null,
  activityFeed: null,
  activityExpanded: false,
  recommendations: [],
  activeDelights: [],
  delightCurrentIndex: 0,
  messages: { notifications: [], delights: [] },
  profile: null,
  chatTurns: [],
  pendingConfirmationCount: 0,
  pendingChatPolls: new Set(),
  pendingChatContext: null,
};

/**
 * Load persisted listMode preference from localStorage.
 * Returns true if user previously switched to list mode.
 * Defaults to list mode on first visit.
 */
function loadListModePreference() {
  try {
    const stored = localStorage.getItem("obc:listMode");
    if (stored === null) return true; // first visit → default to list mode
    return stored === "true";
  } catch {
    return true;
  }
}

/** Persist listMode preference. */
export function persistListMode(listMode) {
  try {
    localStorage.setItem("obc:listMode", String(listMode));
  } catch { /* storage unavailable */ }
}

// Apply persisted preference so apps/default state includes it.
state.listMode = loadListModePreference();

const listeners = new Set();

/**
 * Shallow-merge partial into state and notify listeners.
 * For Set/Array fields, callers must pass a new collection (no in-place mutation).
 */
export function patchState(partial) {
  if (!partial || typeof partial !== "object") return;
  Object.assign(state, partial);
  for (const fn of listeners) {
    try { fn(state, partial); } catch { /* listener errors don't block others */ }
  }
}

/**
 * Subscribe to state changes. Returns an unsubscribe function.
 * @param {(state: object, changed: object) => void} listener
 */
export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
