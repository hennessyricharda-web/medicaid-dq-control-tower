"use client";
/** 44 px header (LAYOUT_SYSTEM §1.1): brand, tenant, search, environment chips, simulated clock, user menu. */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { MOCK } from "@/lib/config";
import { useTenant } from "@/lib/tenant";
import { PREF, applyTheme, usePref, type Theme } from "@/lib/prefs";
import { Mark } from "./Icon";
import { useShellData } from "./ShellData";

/** Who this agent serves, from GET /tenant at runtime (the repository carries no tenant names). */
function TenantBlock() {
  const { tenant, error } = useTenant();
  if (error)
    return (
      <span className="tenant" title={error}>
        <b>Tenant unavailable</b>
      </span>
    );
  if (!tenant)
    return (
      <span className="tenant">
        <b>Loading tenant…</b>
      </span>
    );
  const sub = [tenant.agency, tenant.division].filter(Boolean).join(", ");
  return (
    <span className="tenant" aria-label="Tenant">
      <b className="tenant-name" title={tenant.display_name ?? undefined}>
        {tenant.display_name ?? "Unnamed tenant"}
      </b>
      {sub ? <span title={sub}>{sub}</span> : null}
    </span>
  );
}

/** Part E genesis_state; pre-Part E servers send only genesis_status, which must equal RATIFIED exactly. */
function GenesisChip() {
  const { summary: s } = useShellData();
  if (!s) return null;
  const state = s.genesis_state ?? s.genesis_status;
  if (state === "RATIFIED") return null;
  const label = s.genesis_label ?? state;
  return (
    <span className="envchip env-test" title={`Genesis state ${state}: the control plane was not seeded by a ratified two-person genesis`}>
      <span aria-hidden="true">!</span> {label}
    </span>
  );
}

/** §10b demo mode only. The Run button is for people with the operations duty; the server enforces it too. */
function SimClock() {
  const { summary: s, operator, simBusy, simMsg, clearSimMsg, runNextNight } = useShellData();
  if (!s?.sim?.enabled) return null;
  return (
    <div className="simclock" title="Demo mode: the business date is simulated">
      <span>Business date</span>
      <b>{s.sim.current_date ?? "none"}</b>
      <span className="simchip">Simulated</span>
      <span className="q" title={`${s.sim.queued} simulated night(s) queued`}>
        {s.sim.queued} queued
      </span>
      {operator ? (
        <button type="button" className="primary" onClick={() => void runNextNight()} disabled={simBusy || s.sim.queued === 0}>
          {simBusy ? "Running…" : "Run next night"}
        </button>
      ) : null}
      {simMsg ? (
        <div role={simMsg.ok ? "status" : "alert"} className={`simmsg${simMsg.ok ? "" : " err"}`}>
          <button type="button" className="x sm ghost" onClick={clearSimMsg} aria-label="Dismiss run message">
            ✕
          </button>
          {simMsg.text}{" "}
          {simMsg.run ? (
            <Link href={`/nightly?run=${encodeURIComponent(simMsg.run)}`} onClick={clearSimMsg}>
              View run
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function initials(name: string): string {
  const p = name.trim().split(/\s+/);
  return ((p[0]?.[0] ?? "") + (p.length > 1 ? (p[p.length - 1]?.[0] ?? "") : "")).toUpperCase() || "?";
}

function UserMenu({ onShortcuts }: { onShortcuts: () => void }) {
  const { session, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = usePref<Theme>(PREF.theme, "system");
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => applyTheme(theme), [theme]);
  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  if (!session)
    return (
      <Link className="signinlink" href="/login">
        Sign in
      </Link>
    );
  const me = session.me;
  const org = me.org_label ?? me.org ?? null;
  const scope = me.authorities.length ? me.authorities.join(", ") : "no authority";
  const next: Theme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  return (
    <div className="who" ref={ref}>
      <button type="button" className="whobtn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)} title={`${me.name} (${me.person_id})`}>
        <span className="avatar" aria-hidden="true">
          {initials(me.name)}
        </span>
        <span>
          <span className="nm">{me.name}</span>
          <small>
            {org ? `${org}, ` : ""}scope {scope}
          </small>
        </span>
      </button>
      {open ? (
        <div className="menu" role="menu" aria-label="Account">
          <div className="mh">
            {me.name} <span className="mono">({me.person_id})</span>
            <div className="hint">Duties: {me.duties.join(", ") || "none"}</div>
          </div>
          <button type="button" role="menuitem" className="mi" onClick={() => setTheme(next)}>
            Theme: {theme} <span className="hint">switch to {next}</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="mi"
            onClick={() => {
              setOpen(false);
              onShortcuts();
            }}
          >
            Shortcuts <span className="kbd">?</span>
          </button>
          <button
            type="button"
            role="menuitem"
            className="mi"
            onClick={() => {
              setOpen(false);
              signOut();
            }}
          >
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function Header({ onSearch, onShortcuts }: { onSearch: () => void; onShortcuts: () => void }) {
  return (
    <header className="hdr">
      <Link className="brandcell" href="/" aria-label="Command Tower home">
        <Mark />
        <span className="bname">Command Tower</span>
      </Link>
      <TenantBlock />
      <button type="button" className="search" onClick={onSearch} aria-label="Search and commands (Ctrl K)">
        <span aria-hidden="true">⌕</span>
        <span>Search units, nodes, screens and actions</span>
        <span className="kbd">Ctrl K</span>
      </button>
      <span className="sp" />
      <GenesisChip />
      {MOCK ? (
        <span className="envchip env-mock mockflag" title="Mock mode: sample data in this browser tab; no API, IdP or database">
          MOCK DATA
        </span>
      ) : null}
      <SimClock />
      <UserMenu onShortcuts={onShortcuts} />
    </header>
  );
}
