/** Page anatomy (LAYOUT_SYSTEM §6): a 44 px page header, an optional toolbar, then content that scrolls in its pane. */
import type { ReactNode } from "react";

export function PageHeader({
  title,
  sub,
  subId,
  crumb,
  scope,
  actions,
  titleId,
}: {
  title: ReactNode;
  /** One line with the key count. */
  sub?: ReactNode;
  subId?: string;
  crumb?: ReactNode;
  /** Which scope the list shows ("Showing AUTH_MEDICAID, your scope"). */
  scope?: ReactNode;
  /** At most one primary and one secondary button. */
  actions?: ReactNode;
  titleId?: string;
}) {
  return (
    <div className="ph">
      {crumb ? <span className="crumb">{crumb}</span> : null}
      <h1 id={titleId}>{title}</h1>
      {sub !== undefined ? (
        <span className="sub" id={subId} aria-live="polite">
          {sub}
        </span>
      ) : null}
      <span className="sp" />
      {scope ? <span className="scope">{scope}</span> : null}
      {actions ? <span className="acts">{actions}</span> : null}
    </div>
  );
}

/** Scrolling body for screens that are not split panes yet (their deeper redesign comes later). */
export function PageBody({ children }: { children: ReactNode }) {
  return <div className="pagebody">{children}</div>;
}
