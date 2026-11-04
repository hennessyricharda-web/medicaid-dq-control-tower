import { DispositionChip } from "./Chip";
import { fmtNum } from "@/lib/format";

/**
 * Renders rows whose shape the spine does not define (e.g. abc_before_after, audit).
 * Columns are the union of keys in arrival order; nothing is invented or dropped.
 */
export function GenericTable({ rows, caption, empty }: { rows: Record<string, unknown>[]; caption: string; empty: string }) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  const cols: string[] = [];
  for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
  return (
    <div className="tablewrap">
      <table>
        <caption className="skip">{caption}</caption>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c} scope="col" className={typeof rows[0]?.[c] === "number" ? "num" : undefined}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => {
                const v = r[c];
                if (c === "disposition" && typeof v === "string")
                  return (
                    <td key={c}>
                      <DispositionChip value={v} />
                    </td>
                  );
                return (
                  <td key={c} className={typeof v === "number" ? "num" : undefined}>
                    {fmtNum(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
