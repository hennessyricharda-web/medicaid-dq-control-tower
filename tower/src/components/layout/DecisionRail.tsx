import type { ReactNode } from "react";

/**
 * The fixed right rail every governed object shares (LAYOUT_SYSTEM §5): approvals needed, who can act and why,
 * the action controls and the step-up. It scrolls on its own and never scrolls away with the evidence.
 */
export function DecisionRail({ title, width, children, label }: { title?: string; width: number; children: ReactNode; label?: string }) {
  return (
    <aside className="drail" style={{ width }} aria-label={label ?? title ?? "Decide"}>
      {title ? <div className="drail-h">{title}</div> : null}
      <div className="drail-b">{children}</div>
    </aside>
  );
}
