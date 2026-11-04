"use client";
/**
 * Ctrl K command palette (LAYOUT_SYSTEM §1.1, §1.4): go to a screen, find a unit or node in the queue already
 * loaded for your scope, or run an action ("Run next night" for operators in demo mode).
 */
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { encUnit } from "@/lib/format";
import { visibleNav } from "@/lib/nav";
import { useShellData } from "./ShellData";

interface Cmd {
  id: string;
  group: "Go to" | "Action" | "Unit" | "Node";
  label: string;
  hint?: string;
  run: () => void;
  text: string;
}

export function CommandPalette({ onClose, onToggleRail, onShortcuts }: { onClose: () => void; onToggleRail: () => void; onShortcuts: () => void }) {
  const router = useRouter();
  const { session, signOut } = useAuth();
  const { demo, operator, summary, queue, runNextNight, simBusy } = useShellData();
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const restore = useRef<Element | null>(null);

  useEffect(() => {
    restore.current = document.activeElement;
    input.current?.focus();
    return () => {
      if (restore.current instanceof HTMLElement) restore.current.focus();
    };
  }, []);

  const cmds = useMemo<Cmd[]>(() => {
    const go = (href: string) => () => {
      onClose();
      router.push(href);
    };
    const out: Cmd[] = visibleNav({ demo }).map((n) => ({
      id: `nav-${n.id}`,
      group: "Go to",
      label: n.label,
      hint: n.key ? `g ${n.key}` : n.group,
      run: go(n.href),
      text: `${n.label} ${n.group} ${n.keywords ?? ""}`,
    }));
    if (demo && operator && summary?.sim?.enabled) {
      out.unshift({
        id: "act-run-night",
        group: "Action",
        label: "Run next night",
        hint: summary.sim.queued ? `${summary.sim.queued} queued` : "nothing queued",
        run: () => {
          onClose();
          if (!simBusy && summary.sim!.queued > 0) void runNextNight();
        },
        text: "run next night simulate nightly business date",
      });
    }
    const then = (f: () => void) => () => {
      onClose();
      f();
    };
    out.push(
      { id: "act-rail", group: "Action", label: "Collapse or expand the navigation", hint: "[", run: then(onToggleRail), text: "rail collapse expand sidebar navigation" },
      { id: "act-keys", group: "Action", label: "Keyboard shortcuts", hint: "?", run: then(onShortcuts), text: "shortcuts keys help" },
    );
    if (session) out.push({ id: "act-signout", group: "Action", label: "Sign out", run: then(signOut), text: "sign out logout" });
    const nodes = new Set<string>();
    for (const c of queue ?? []) {
      nodes.add(c.node);
      for (const u of c.units) out.push({ id: `unit-${u}`, group: "Unit", label: u, hint: c.node, run: go(`/units/${encUnit(u)}`), text: `${u} ${c.node} ${c.rule_ids.join(" ")}` });
    }
    for (const n of nodes) out.push({ id: `node-${n}`, group: "Node", label: n, hint: "filter the queue", run: go(`/?q=${encodeURIComponent(n)}`), text: n });
    return out;
  }, [demo, operator, summary, queue, session, router, onClose, onToggleRail, onShortcuts, signOut, runNextNight, simBusy]);

  const shown = useMemo(() => {
    const terms = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = terms.length ? cmds.filter((c) => terms.every((t) => c.text.toLowerCase().includes(t))) : cmds.filter((c) => c.group === "Go to" || c.group === "Action");
    const list = hits.slice(0, 40);
    if (q.includes(":") && !list.some((c) => c.label === q.trim())) {
      list.push({
        id: "open-key",
        group: "Unit",
        label: `Open unit ${q.trim()}`,
        hint: "by unit key",
        run: () => {
          onClose();
          router.push(`/units/${encUnit(q.trim())}`);
        },
        text: q,
      });
    }
    return list;
  }, [q, cmds, onClose, router]);

  useEffect(() => setActive(0), [q]);

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(shown.length - 1, a + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      shown[active]?.run();
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog palette" role="dialog" aria-modal="true" aria-label="Command palette" onKeyDown={onKey}>
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={shown[active] ? `pc-${active}` : undefined}
          placeholder="Search units, nodes, screens and actions"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        {shown.length ? (
          <ul id="palette-list" role="listbox" aria-label="Results">
            {shown.map((c, i) => (
              <li
                key={c.id}
                id={`pc-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  c.run();
                }}
              >
                <span className="grp">{c.group}</span>
                <span className={c.group === "Unit" || c.group === "Node" ? "mono" : undefined}>{c.label}</span>
                {c.hint ? <span className="k">{c.hint}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="none">Nothing matches. Unit keys contain a colon; type one to open it directly.</p>
        )}
      </div>
    </div>
  );
}
