import { ToneChip } from "./Chip";

/** Sensitivity class. part2 is badged: its units never feed explanations, exports or the blackboard. */
export function ClassChip({ c }: { c: string }) {
  if (c === "part2") return <ToneChip tone="crit" label="part2" title="42 CFR Part 2 class: values never leave the data plane; masked on reveal" />;
  const masked = c === "identity" || c === "clinical";
  return <ToneChip tone={masked ? "warn" : "neutral"} label={c} title={masked ? `${c}: masked on reveal` : `class ${c}`} />;
}
