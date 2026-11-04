"use client";
/**
 * Per-viewer layout preferences (LAYOUT_SYSTEM §1.3, §3, §5): rail collapse, table density, split-pane widths,
 * theme and the single-key shortcut switch (WCAG 2.1.4). Conveniences only: nothing here is a secret, nothing is
 * shared, and every read falls back to a default when browser storage is unavailable. The bearer token is never
 * stored (lib/auth.tsx).
 */
import { useCallback, useEffect, useState } from "react";

export type Density = "compact" | "default" | "comfortable";
export type Theme = "system" | "light" | "dark";

const PREFIX = "dq.tower.";
const EVENT = "dq:prefs";

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

function write<T>(key: string, value: T): void {
  try {
    window.localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* storage blocked: the preference lasts for this tab only */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: key }));
}

/** A preference that persists per viewer and stays in step across components in the same tab. */
export function usePref<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(fallback);
  useEffect(() => {
    setValue(read(key, fallback));
    const on = (e: Event) => {
      if ((e as CustomEvent<string>).detail === key) setValue(read(key, fallback));
    };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = useCallback(
    (v: T) => {
      setValue(v);
      write(key, v);
    },
    [key],
  );
  return [value, set];
}

export const PREF = {
  railCollapsed: "rail.collapsed",
  shortcuts: "shortcuts.enabled",
  theme: "theme",
  density: (table: string) => `density.${table}`,
  split: (id: string) => `split.${id}`,
  /** K4 (PFP P2-4): "table" or "graph" for /pipelines/[id]/graph. Table/Outline is the accessible default --
   *  this preference can only ever SWITCH TO graph, never override reduced-motion, forced-colors or a
   *  keyboard-first interaction (those checks happen at render time in the graph page, not here). */
  graphView: "pipelines.graphView",
} as const;

/** Applies the theme preference to <html data-theme>; "system" defers to prefers-color-scheme. */
export function applyTheme(t: Theme): void {
  const el = document.documentElement;
  if (t === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", t);
}

/** Single-character shortcuts are inactive while focus is in a text field (LAYOUT_SYSTEM §1.4). */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag === "INPUT") {
    const type = (t as HTMLInputElement).type;
    return !["checkbox", "radio", "button", "submit", "reset", "range"].includes(type);
  }
  return false;
}
