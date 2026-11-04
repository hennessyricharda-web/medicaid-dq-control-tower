import { ToneChip } from "./Chip";
import { crStatusTone } from "@/lib/governance";

export function LooseningBadge() {
  return <ToneChip tone="warn" label="LOOSENING" title="Reduces what is checked or who must look: needs two approvers" />;
}

export function OriginChip({ origin }: { origin: string }) {
  return <ToneChip tone={origin === "HUMAN" ? "neutral" : "info"} label={origin} title={`Origin ${origin}`} />;
}

export function CrStatusChip({ status }: { status: string }) {
  return <ToneChip tone={crStatusTone(status)} label={status} title={`Change request status ${status}`} />;
}

/** content {rule, hash, reason}: the version named is withdrawn from approval; releases under it go to look-back. */
export function RevocationLine({ content }: { content: unknown }) {
  const c = (content ?? {}) as { rule?: string; hash?: string; reason?: string };
  return (
    <p className="banner t-warn revoke" role="note" style={{ margin: "0 0 8px" }}>
      Revoke <span className="mono">{c.rule ?? "?"}</span> version <code title={c.hash}>{(c.hash ?? "?").slice(0, 12)}</code>: {c.reason ?? "(no reason)"}
      <span className="hint" style={{ display: "block", fontWeight: 400 }}>
        Once applied, new releases under this version are blocked and every release already made under it opens a look-back item.
      </span>
    </p>
  );
}

