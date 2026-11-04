"use client";
/** The `?` shortcut sheet (LAYOUT_SYSTEM §1.4). Single-key shortcuts can be turned off here (WCAG 2.1.4). */
import { useEffect, useRef } from "react";
import { NAV_ITEMS } from "@/lib/nav";
import { PREF, usePref } from "@/lib/prefs";

const KEYS: [string, string][] = [
  ["Ctrl K", "Command palette: go to, search, run actions"],
  ["/", "Focus the page filter"],
  ["g then a letter", "Go to a screen (listed below)"],
  ["j / k or ↓ / ↑", "Move the selection in a list"],
  ["Enter", "Open the selection"],
  ["x", "Toggle row selection (where a list has checkboxes)"],
  ["[", "Collapse or expand the navigation"],
  ["← / →", "Resize a split pane when its divider has focus"],
  ["?", "This sheet"],
  ["Esc", "Close a popover, dialog or drawer"],
];

export function ShortcutSheet({ onClose }: { onClose: () => void }) {
  const [enabled, setEnabled] = usePref<boolean>(PREF.shortcuts, true);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement;
    ref.current?.focus();
    return () => {
      if (prev instanceof HTMLElement) prev.focus();
    };
  }, []);
  const go = NAV_ITEMS.filter((n) => n.built && n.key);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sheet-h"
        tabIndex={-1}
        ref={ref}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      >
        <div className="dh">
          <h2 id="sheet-h">Keyboard shortcuts</h2>
          <button type="button" className="sm ghost" onClick={onClose} aria-label="Close shortcuts">
            ✕
          </button>
        </div>
        <div className="sheet">
          <p>
            <button type="button" role="switch" aria-checked={enabled} className="switch" onClick={() => setEnabled(!enabled)}>
              <span className="sw" aria-hidden="true" />
              Single-key shortcuts {enabled ? "on" : "off"}
            </button>
            <span className="hint"> Ctrl K and Esc always work. Single keys never fire while you type in a field.</span>
          </p>
          <table>
            <tbody>
              {KEYS.map(([k, v]) => (
                <tr key={k}>
                  <th scope="row" style={{ width: 160 }}>
                    <span className="kbd">{k}</span>
                  </th>
                  <td>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 style={{ marginTop: 12 }}>Go to</h3>
          <table>
            <tbody>
              {go.map((n) => (
                <tr key={n.id}>
                  <th scope="row" style={{ width: 160 }}>
                    <span className="kbd">g {n.key}</span>
                  </th>
                  <td>
                    {n.label} <span className="hint">({n.group})</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
