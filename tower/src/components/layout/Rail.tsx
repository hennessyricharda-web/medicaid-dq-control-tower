"use client";
/** Grouped navigation rail (LAYOUT_SYSTEM §1.3), driven by the lib/nav.ts registry. 212 px, collapses to 52 px. */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_GROUPS, isCurrent, visibleNav, type NavItem } from "@/lib/nav";
import { Icon } from "./Icon";
import { useShellData } from "./ShellData";

function Badge({ item, id }: { item: NavItem; id: string }) {
  const { summary, lookbackOpen, demo } = useShellData();
  switch (item.badge) {
    case "decisions_due": {
      const n = summary?.decisions_due;
      if (!n) return null;
      const overdue = typeof summary?.oldest_sla_remaining_min === "number" && summary.oldest_sla_remaining_min < 0;
      return (
        <span id={id} className={`n${overdue ? " bad" : ""}`} title={overdue ? `${n} due, at least one past SLA` : `${n} due`}>
          {n}
        </span>
      );
    }
    case "lookback_open":
      return lookbackOpen ? (
        <span id={id} className="n warn" title={`${lookbackOpen} open look-back item(s)`}>
          {lookbackOpen}
        </span>
      ) : null;
    case "abc_out": {
      const n = summary?.abc.out_of_balance;
      return n ? (
        <span id={id} className="n bad" title={`${n} out of balance`}>
          {n}
        </span>
      ) : null;
    }
    case "demo":
      return demo ? <span id={id} className="n">Demo</span> : null;
    default:
      return null;
  }
}

export function Rail({ collapsed, onToggle, onShortcuts }: { collapsed: boolean; onToggle: () => void; onShortcuts: () => void }) {
  const path = usePathname() ?? "/";
  const { demo } = useShellData();
  const items = visibleNav({ demo });
  return (
    <nav className="rail" aria-label="Primary">
      {NAV_GROUPS.map((g) => {
        const list = items.filter((i) => i.group === g);
        if (!list.length) return null;
        return (
          <div key={g} role="group" aria-labelledby={`rail-${g}`}>
            <p className="grp" id={`rail-${g}`}>
              {g}
            </p>
            <ul>
              {list.map((n) => {
                const cur = isCurrent(n, path);
                return (
                  <li key={n.id}>
                    <Link
                      href={n.href}
                      aria-current={cur ? "page" : undefined}
                      title={collapsed ? n.label : n.key ? `${n.label} (g ${n.key})` : n.label}
                      aria-label={n.label}
                      aria-describedby={n.badge ? `badge-${n.id}` : undefined}
                    >
                      <Icon name={n.icon} />
                      <span className="lbl">{n.label}</span>
                      <Badge item={n} id={`badge-${n.id}`} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="foot">
        <button type="button" onClick={onShortcuts} aria-label="Keyboard shortcuts (?)" title="Keyboard shortcuts (?)">
          <span className="kbd" aria-hidden="true">
            ?
          </span>
          <span className="lbl">Shortcuts</span>
        </button>
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expand navigation ([)" : "Collapse navigation ([)"}
          aria-expanded={!collapsed}
          title={collapsed ? "Expand navigation ([)" : "Collapse navigation ([)"}
          data-rail-toggle
        >
          <span aria-hidden="true">{collapsed ? "›" : "‹"}</span>
        </button>
      </div>
    </nav>
  );
}
