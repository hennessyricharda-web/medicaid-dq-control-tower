"use client";
/**
 * Dense table (LAYOUT_SYSTEM §3): one line per row, sticky 30 px header, density modes (28/32/40 px), column
 * priority (p2 hides below 1600 px, p3 below 1800 px), collapsible group rows, an expanding row, single selection
 * with j/k/↑/↓ and Enter, an optional checkbox column (x), and windowed rendering above 200 rows.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Density } from "@/lib/prefs";
import { LIST_MOVE_EVENT } from "./events";

export interface Column<T> {
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** 1 always shows; 2 hides below 1600 px viewport; 3 below 1800 px. Hidden values stay in the detail pane. */
  p?: 1 | 2 | 3;
  num?: boolean;
  mono?: boolean;
  width?: number | string;
  title?: (row: T) => string | undefined;
}

export interface Group<T> {
  id: string;
  label: ReactNode;
  rows: T[];
}

const ROW_PX: Record<Density, number> = { compact: 28, default: 32, comfortable: 40 };
const GROUP_PX = 30;
const EXPANDED_PX = 120;
const VIRTUAL_OVER = 200;

type Item<T> = { kind: "group"; g: Group<T> } | { kind: "row"; r: T; key: string } | { kind: "x"; r: T; key: string; node: ReactNode };

function isEmpty(v: ReactNode): boolean {
  return v === null || v === undefined || v === "" || v === false;
}

export function DataTable<T>({
  columns,
  rows,
  groups,
  rowKey,
  selected,
  onSelect,
  onOpen,
  expand,
  checked,
  onCheck,
  density = "default",
  caption,
  rowClass,
  empty,
  primary,
  label,
}: {
  columns: Column<T>[];
  rows?: T[];
  groups?: Group<T>[];
  rowKey: (r: T) => string;
  selected?: string | null;
  onSelect?: (key: string) => void;
  onOpen?: (key: string) => void;
  /** Content shown under a row (one at a time by default: the caller decides which). */
  expand?: (r: T) => ReactNode | null;
  checked?: Set<string>;
  onCheck?: (key: string, on: boolean) => void;
  density?: Density;
  caption: string;
  rowClass?: (r: T) => string | undefined;
  empty?: ReactNode;
  /** The page's main list: j / k move it from anywhere on the page. */
  primary?: boolean;
  label?: string;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const wrap = useRef<HTMLDivElement>(null);
  const [scroll, setScroll] = useState({ top: 0, height: 800 });

  const allGroups: Group<T>[] = useMemo(() => groups ?? [{ id: "", label: null, rows: rows ?? [] }], [groups, rows]);
  const flatRows = useMemo(() => allGroups.flatMap((g) => (collapsed.has(g.id) ? [] : g.rows)), [allGroups, collapsed]);
  const keys = useMemo(() => flatRows.map(rowKey), [flatRows, rowKey]);
  const total = useMemo(() => allGroups.reduce((n, g) => n + g.rows.length, 0), [allGroups]);

  const items: Item<T>[] = useMemo(() => {
    const out: Item<T>[] = [];
    for (const g of allGroups) {
      if (groups) out.push({ kind: "group", g });
      if (collapsed.has(g.id)) continue;
      for (const r of g.rows) {
        const key = rowKey(r);
        out.push({ kind: "row", r, key });
        const node = expand?.(r);
        if (node) out.push({ kind: "x", r, key, node });
      }
    }
    return out;
  }, [allGroups, groups, collapsed, rowKey, expand]);

  const move = useCallback(
    (d: number) => {
      if (!keys.length || !onSelect) return;
      const i = selected ? keys.indexOf(selected) : -1;
      const next = keys[Math.max(0, Math.min(keys.length - 1, i < 0 ? 0 : i + d))];
      if (next) onSelect(next);
    },
    [keys, selected, onSelect],
  );

  useEffect(() => {
    if (!primary) return;
    const on = (e: Event) => move((e as CustomEvent<number>).detail);
    window.addEventListener(LIST_MOVE_EVENT, on);
    return () => window.removeEventListener(LIST_MOVE_EVENT, on);
  }, [primary, move]);

  // Keep the selected row in view.
  useEffect(() => {
    if (!selected || !wrap.current) return;
    const el = wrap.current.querySelector<HTMLElement>(`tr[data-key="${CSS.escape(selected)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const on = () => setScroll({ top: el.scrollTop, height: el.clientHeight });
    on();
    el.addEventListener("scroll", on, { passive: true });
    const ro = new ResizeObserver(on);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", on);
      ro.disconnect();
    };
  }, []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.target instanceof HTMLElement && e.target.closest("input, select, textarea, button, a") && e.target !== e.currentTarget) return;
    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault();
      move(1);
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault();
      move(-1);
    } else if (e.key === "Enter" && selected && onOpen) {
      e.preventDefault();
      onOpen(selected);
    } else if (e.key === "x" && selected && onCheck) {
      e.preventDefault();
      onCheck(selected, !checked?.has(selected));
    }
  }

  // Windowing: only above 200 data rows. Heights are estimated; expanded rows use a generous estimate.
  const virtual = total > VIRTUAL_OVER;
  const rowPx = ROW_PX[density];
  const heights = items.map((it) => (it.kind === "group" ? GROUP_PX : it.kind === "row" ? rowPx : EXPANDED_PX));
  let start = 0;
  let end = items.length;
  let padTop = 0;
  let padBottom = 0;
  if (virtual) {
    const over = 10 * rowPx;
    let y = 0;
    start = items.length;
    for (let i = 0; i < items.length; i++) {
      const h = heights[i] ?? rowPx;
      if (y + h >= scroll.top - over && start === items.length) {
        start = i;
        padTop = y;
      }
      if (y > scroll.top + scroll.height + over) {
        end = i;
        break;
      }
      y += h;
    }
    if (start === items.length) start = 0;
    const totalH = heights.reduce((a, b) => a + b, 0);
    const shownH = heights.slice(start, end).reduce((a, b) => a + b, 0);
    padBottom = Math.max(0, totalH - padTop - shownH);
  }
  const ncols = columns.length + (onCheck ? 1 : 0);
  const cls = (c: Column<T>) => [c.p === 2 ? "p2" : c.p === 3 ? "p3" : "", c.num ? "num" : "", c.mono ? "mono" : ""].filter(Boolean).join(" ") || undefined;

  return (
    <div
      className="tw"
      ref={wrap}
      tabIndex={0}
      data-list
      onKeyDown={onKeyDown}
      aria-label={label ?? caption}
      role="region"
    >
      <table className={`dt${density === "default" ? "" : ` ${density}`}`}>
        <caption className="sr">{caption}</caption>
        <thead>
          <tr>
            {onCheck ? (
              <th className="cb" scope="col">
                <span className="sr">Select</span>
              </th>
            ) : null}
            {columns.map((c) => (
              <th key={c.id} scope="col" className={cls({ ...c, mono: false })} style={c.width ? { width: c.width } : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {padTop > 0 ? (
            <tr className="spacer" aria-hidden="true">
              <td colSpan={ncols} style={{ height: padTop }} />
            </tr>
          ) : null}
          {items.slice(start, end).map((it) => {
            if (it.kind === "group") {
              const open = !collapsed.has(it.g.id);
              return (
                <tr key={`g-${it.g.id}`} className="grp" role="rowgroup">
                  <td colSpan={ncols}>
                    <button
                      type="button"
                      aria-expanded={open}
                      aria-label={open ? "Collapse group" : "Expand group"}
                      onClick={() =>
                        setCollapsed((s) => {
                          const n = new Set(s);
                          if (n.has(it.g.id)) n.delete(it.g.id);
                          else n.add(it.g.id);
                          return n;
                        })
                      }
                    >
                      <span aria-hidden="true">{open ? "▾" : "▸"}</span>
                    </button>{" "}
                    {it.g.label}
                  </td>
                </tr>
              );
            }
            if (it.kind === "x") {
              return (
                <tr key={`x-${it.key}`} className="xrow">
                  <td colSpan={ncols}>{it.node}</td>
                </tr>
              );
            }
            const r = it.r;
            const sel = it.key === selected;
            const extra = rowClass?.(r);
            return (
              <Fragment key={it.key}>
                <tr
                  className={["r", sel ? "sel" : "", extra ?? ""].filter(Boolean).join(" ")}
                  data-key={it.key}
                  aria-selected={sel}
                  onClick={() => onSelect?.(it.key)}
                  onDoubleClick={() => onOpen?.(it.key)}
                >
                  {onCheck ? (
                    <td className="cb" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" checked={!!checked?.has(it.key)} onChange={(e) => onCheck(it.key, e.target.checked)} aria-label={`Select ${it.key}`} />
                    </td>
                  ) : null}
                  {columns.map((c) => {
                    const v = c.cell(r);
                    return (
                      <td key={c.id} className={cls(c)} title={c.title?.(r)} style={c.width ? { width: c.width, maxWidth: c.width } : undefined}>
                        {isEmpty(v) ? <span className="dash">–</span> : v}
                      </td>
                    );
                  })}
                </tr>
              </Fragment>
            );
          })}
          {padBottom > 0 ? (
            <tr className="spacer" aria-hidden="true">
              <td colSpan={ncols} style={{ height: padBottom }} />
            </tr>
          ) : null}
          {total === 0 && empty ? (
            <tr>
              <td colSpan={ncols} style={{ height: "auto", whiteSpace: "normal" }}>
                {empty}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

/** A long identifier truncated in the middle (`ZZ:MEDICAID:a3f19c…0002377`), full value in the tooltip. */
export function MidTrunc({ value, keep = 30 }: { value: string; keep?: number }) {
  if (value.length <= keep) return <span className="mono">{value}</span>;
  const head = Math.ceil((keep - 1) * 0.6);
  const tail = keep - 1 - head;
  return (
    <span className="mono" title={value}>
      {value.slice(0, head)}…{value.slice(-tail)}
    </span>
  );
}
