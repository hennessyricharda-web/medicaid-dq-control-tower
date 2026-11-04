"use client";
/** Toolbar primitives (LAYOUT_SYSTEM §6.3). */
import type { ReactNode } from "react";

/** Toolbar row (38 px): filter field, filter chips, then sort and density on the right. */
export function Toolbar({ children, label = "Filters" }: { children: ReactNode; label?: string }) {
  return (
    <div className="tb" role="toolbar" aria-label={label}>
      {children}
    </div>
  );
}

/** The page filter field; `/` focuses it from anywhere. */
export function FilterField({ value, onChange, placeholder, label }: { value: string; onChange: (v: string) => void; placeholder: string; label: string }) {
  return (
    <span className="fld q">
      <input
        type="search"
        data-page-filter
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            onChange("");
            (e.target as HTMLInputElement).blur();
          }
        }}
      />
      <span className="kbd" aria-hidden="true">
        /
      </span>
    </span>
  );
}

/** A filter chip that shows its value ("Mode All"), backed by a native select for keyboard and screen readers. */
export function SelectChip<V extends string>({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: V | "";
  options: { value: V; label: string }[];
  onChange: (v: V | "") => void;
}) {
  return (
    <span className={`fld${value ? " on" : ""}`}>
      <label htmlFor={id} style={{ margin: 0, fontWeight: 400, fontSize: "inherit", color: "inherit" }}>
        {label}
      </label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as V | "")}>
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </span>
  );
}

/** Segmented control (density, view). */
export function Seg<V extends string>({ label, value, options, onChange }: { label: string; value: V; options: { value: V; label: string }[]; onChange: (v: V) => void }) {
  return (
    <span className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </span>
  );
}
