"use client";
/**
 * List | detail split pane (LAYOUT_SYSTEM §5). The divider drags between 320 px and 60% of the pane, answers
 * ←/→ when focused (WCAG 2.5.7; Shift for bigger steps, Home/End for the limits) and remembers its width per
 * viewer. The selection lives in the URL as `?sel=`, so a link opens the same view and Back works.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { PREF, usePref } from "@/lib/prefs";

const MIN = 320;
const MAX_FRAC = 0.6;

export function SplitPane({
  id,
  first,
  second,
  fixed = "second",
  defaultSize,
  label,
  drawerable,
}: {
  /** Preference key: the width persists per viewer per screen. */
  id: string;
  first: ReactNode;
  second: ReactNode;
  /** Which pane has the fixed width the divider controls; the other one flexes. */
  fixed?: "first" | "second";
  defaultSize: number;
  /** Names for the two landmark sections. */
  label: [string, string];
  /** Below 1280 px the fixed (detail) pane overlays the list as a drawer. */
  drawerable?: boolean;
}) {
  const [stored, setStored] = usePref<number>(PREF.split(id), defaultSize);
  const [size, setSize] = useState(defaultSize);
  const [drag, setDrag] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setSize(stored), [stored]);

  const clamp = useCallback((w: number) => {
    const total = box.current?.clientWidth ?? 1440;
    return Math.round(Math.max(MIN, Math.min(w, Math.max(MIN, total * MAX_FRAC))));
  }, []);

  const commit = useCallback(
    (w: number) => {
      const c = clamp(w);
      setSize(c);
      setStored(c);
    },
    [clamp, setStored],
  );

  function onPointerDown(e: React.PointerEvent<HTMLButtonElement>) {
    e.preventDefault();
    const startX = e.clientX;
    const start = size;
    setDrag(true);
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - startX;
      setSize(clamp(fixed === "second" ? start - dx : start + dx));
    };
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      setDrag(false);
      const dx = ev.clientX - startX;
      commit(fixed === "second" ? start - dx : start + dx);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    const step = e.shiftKey ? 64 : 16;
    // ← moves the divider left: the right-hand fixed pane grows, a left-hand fixed pane shrinks.
    const grow = fixed === "second" ? 1 : -1;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      commit(size + grow * step);
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      commit(size - grow * step);
    } else if (e.key === "Home") {
      e.preventDefault();
      commit(fixed === "second" ? Number.MAX_SAFE_INTEGER : MIN);
    } else if (e.key === "End") {
      e.preventDefault();
      commit(fixed === "second" ? MIN : Number.MAX_SAFE_INTEGER);
    }
  }

  const total = box.current?.clientWidth ?? 1440;
  const fixedPane = (node: ReactNode, name: string, extra = "") => (
    <section className={`pane fixed ${extra}`} style={{ width: size }} aria-label={name}>
      {node}
    </section>
  );
  const flexPane = (node: ReactNode, name: string) => (
    <section className="pane flex" aria-label={name}>
      {node}
    </section>
  );
  const divider = (
    <button
      type="button"
      className={`divider${drag ? " drag" : ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={`Resize ${label[fixed === "second" ? 1 : 0]}`}
      aria-valuemin={MIN}
      aria-valuemax={Math.round(total * MAX_FRAC)}
      aria-valuenow={size}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => commit(defaultSize)}
      title="Drag, or focus and use ← / →. Double-click to reset."
      data-split={id}
    />
  );
  return (
    <div className={`sp${drawerable ? " drawerable" : ""}`} ref={box}>
      {fixed === "second" ? flexPane(first, label[0]) : fixedPane(first, label[0])}
      {divider}
      {fixed === "second" ? fixedPane(second, label[1], "detailpane") : flexPane(second, label[1])}
    </div>
  );
}

/**
 * The `?sel=` selection, kept in the URL with history.replaceState (Next keeps useSearchParams in step), never a
 * reload: the session lives in this tab. Needs a Suspense boundary above it (AppShell provides one).
 */
export function useSel(param = "sel"): [string | null, (v: string | null) => void] {
  const sp = useSearchParams();
  const fromUrl = sp?.get(param) ?? null;
  const [sel, setLocal] = useState<string | null>(fromUrl);
  useEffect(() => setLocal(fromUrl), [fromUrl]);
  const set = useCallback(
    (v: string | null) => {
      setLocal(v);
      const url = new URL(window.location.href);
      if (v === null) url.searchParams.delete(param);
      else url.searchParams.set(param, v);
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    },
    [param],
  );
  return [sel, set];
}

/** A URL parameter (e.g. `?q=` from the command palette). */
export function useUrlParam(name: string): string | null {
  return useSearchParams()?.get(name) ?? null;
}
