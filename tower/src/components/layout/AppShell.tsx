"use client";
/**
 * App shell (LAYOUT_SYSTEM §1): a 212|1fr by 44|28|1fr grid. Header, status strip, rail, then the page.
 * At 1440x900 page content starts at y=116 (44 + 28 + the 44 px page header).
 * Global keys (§1.4): Ctrl K, `[`, `?`, `/`, `g` + letter, j/k. Single keys are off inside text fields and can
 * be switched off in the shortcut sheet.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { navByKey } from "@/lib/nav";
import { PREF, isTypingTarget, usePref } from "@/lib/prefs";
import { shortHash } from "@/lib/format";
import { CommandPalette } from "./CommandPalette";
import { LIST_MOVE_EVENT } from "./events";
import { Header } from "./Header";
import { Rail } from "./Rail";
import { ShellDataProvider, useShellData } from "./ShellData";
import { ShortcutSheet } from "./ShortcutSheet";
import { StatusStrip } from "./StatusStrip";


/** AUDIT §15: the drift banner shows only on Govern screens; elsewhere the strip's Ruleset slot tints. */
function DriftBanner() {
  const path = usePathname() ?? "";
  const { summary: s } = useShellData();
  if (!s?.drift || !(path.startsWith("/governance") || path.startsWith("/rules"))) return null;
  return (
    <div role="alert" className="banner t-warn drift">
      <strong>
        <span aria-hidden="true">! </span>Ruleset drift.
      </strong>{" "}
      {s.drift}. The runtime still compiles only the approved configuration
      {s.ruleset_version ? (
        <>
          {" "}
          (<code title={s.ruleset_version}>{shortHash(s.ruleset_version, 10)}</code>)
        </>
      ) : null}
      ; bring the change through a <Link href="/governance">change request</Link> to approve it.
    </div>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { demo } = useShellData();
  const [collapsed, setCollapsed] = usePref<boolean>(PREF.railCollapsed, false);
  const [shortcuts] = usePref<boolean>(PREF.shortcuts, true);
  const [palette, setPalette] = useState(false);
  const [sheet, setSheet] = useState(false);
  const chord = useRef<number>(0);

  const toggleRail = useCallback(() => setCollapsed(!collapsed), [collapsed, setCollapsed]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSheet(false);
        setPalette((v) => !v);
        return;
      }
      if (palette || sheet || !shortcuts || e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      if (document.querySelector(".overlay")) return;
      const now = Date.now();
      if (chord.current && now - chord.current < 1200) {
        chord.current = 0;
        const item = navByKey(e.key.toLowerCase(), { demo });
        if (item) {
          e.preventDefault();
          router.push(item.href);
        }
        return;
      }
      switch (e.key) {
        case "g":
          chord.current = now;
          return;
        case "[":
          e.preventDefault();
          toggleRail();
          return;
        case "?":
          e.preventDefault();
          setSheet(true);
          return;
        case "/": {
          const f = document.querySelector<HTMLElement>("[data-page-filter]");
          if (f) {
            e.preventDefault();
            f.focus();
          }
          return;
        }
        case "j":
        case "k": {
          if (e.target instanceof HTMLElement && e.target.closest("[data-list]")) return; // the list handles its own keys
          e.preventDefault();
          window.dispatchEvent(new CustomEvent(LIST_MOVE_EVENT, { detail: e.key === "j" ? 1 : -1 }));
          return;
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [palette, sheet, shortcuts, demo, router, toggleRail]);

  return (
    <div className={`app${collapsed ? " rail-min" : ""}`}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header onSearch={() => setPalette(true)} onShortcuts={() => setSheet(true)} />
      <StatusStrip />
      <Rail collapsed={collapsed} onToggle={toggleRail} onShortcuts={() => setSheet(true)} />
      <main id="main" className="main" tabIndex={-1}>
        <DriftBanner />
        <Suspense fallback={null}>{children}</Suspense>
      </main>
      {palette ? <CommandPalette onClose={() => setPalette(false)} onToggleRail={toggleRail} onShortcuts={() => setSheet(true)} /> : null}
      {sheet ? <ShortcutSheet onClose={() => setSheet(false)} /> : null}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <ShellDataProvider>
      <Frame>{children}</Frame>
    </ShellDataProvider>
  );
}
