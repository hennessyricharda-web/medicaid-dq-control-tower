import type { ReactNode } from "react";

/** LAYOUT_SYSTEM §7: a centred heading, one line and at most one action; `inline` for filtered-empty inside a table. */
export function EmptyState({ title, line, action, inline }: { title: string; line?: ReactNode; action?: ReactNode; inline?: boolean }) {
  return (
    <div className={`empty${inline ? " inline" : ""}`} role="note">
      <h2>{title}</h2>
      {line ? <p style={{ margin: 0 }}>{line}</p> : null}
      {action ?? null}
    </div>
  );
}
