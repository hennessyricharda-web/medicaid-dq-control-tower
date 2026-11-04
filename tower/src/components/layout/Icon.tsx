import type { IconName } from "@/lib/nav";

/** 16 px stroke icons for the rail and palette. Decorative: every use sits beside a text label or a tooltip. */
const PATHS: Record<IconName, string> = {
  queue: "M3 4h10M3 8h10M3 12h6",
  lookback: "M3 8a5 5 0 1 0 1.5-3.6M3 2.5v2.5h2.5M8 5v3l2 1.5",
  deliveries: "M2.5 5 8 2.5 13.5 5v6L8 13.5 2.5 11zM2.5 5 8 7.5 13.5 5M8 7.5v6",
  balance: "M8 2.5v11M4 13.5h8M3 5h10M3 5l-1.5 4h3zM13 5l-1.5 4h3z",
  moon: "M12.5 9.5A5 5 0 0 1 6.5 3.5a5 5 0 1 0 6 6z",
  pulse: "M1.5 8h3l1.5-4 3 8 1.5-4h4",
  grid: "M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z",
  trace: "M2.5 4h7M4.5 8h7M6.5 12h7",
  eval: "M3 13V8M6.5 13V4M10 13V9.5M13.5 13V6",
  shield: "M8 2 13 4v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4z",
  layers: "M8 2.5 13.5 5.5 8 8.5 2.5 5.5zM2.5 8.5 8 11.5l5.5-3M2.5 11 8 14l5.5-3",
  graph: "M4 4h.01M12 4h.01M8 12h.01M4 4l4 8 4-8M4 4h8",
  user: "M8 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM3 13.5c.8-2.4 2.7-3.5 5-3.5s4.2 1.1 5 3.5",
  backtest: "M3 8a5 5 0 1 1 5 5M3 13V9.5h3.5",
  drift: "M2 6c2-2 4 2 6 0s4 2 6 0M2 10c2-2 4 2 6 0s4 2 6 0",
  plug: "M6 2.5v3M10 2.5v3M4.5 5.5h7v2.5a3.5 3.5 0 0 1-7 0zM8 11.5v2",
  link: "M7 9a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-.5.5M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.5-.5",
  branch: "M4.5 3v10M4.5 9c0-2.5 7-1 7-4.5M11.5 3v1.5M4.5 13h.01",
  doc: "M4 2h5.5L12 4.5V14H4zM6 7h4M6 9.5h4M6 12h2.5",
  gear: "M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  flag: "M3.5 14V2.5M3.5 3h8l-1.5 3 1.5 3h-8",
  ledger: "M3 2.5h10v11H3zM5.5 5.5h5M5.5 8h5M5.5 10.5h3",
  check: "M8 2 13 4v4c0 3-2.2 5-5 6-2.8-1-5-3-5-6V4zM5.5 8l1.8 1.8L10.5 6.5",
  chart: "M2.5 13.5h11M4 11l3-4 2.5 2 3.5-5",
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Product mark: a tower with a signal arc. */
export function Mark() {
  return (
    <svg className="mark" viewBox="0 0 22 22" aria-hidden="true" focusable="false">
      <path d="M9 20h4l-1-9h-2z" fill="#8fb3e6" />
      <rect x="8" y="8" width="6" height="3" rx="1" fill="#cfe0f7" />
      <path d="M6 6.5a7 7 0 0 1 10 0M8 8a4 4 0 0 1 6 0" stroke="#cfe0f7" strokeWidth="1.4" fill="none" strokeLinecap="round" />
    </svg>
  );
}
