/**
 * K8/K11 (PARTF_K7_K12_MOCK_CONTRACT.md): the ONE shared marker for every screen or control built against invented
 * fixtures with no real backend yet (K8's profile-edit draft, K11's evaluations history/diff, the annotation
 * queue, and the decision-card feedback control). Structural, not a small badge: a full-width, thick-bordered band
 * that cannot be mistaken for an ordinary informational banner. When the real backend for a capability lands, the
 * next person removes exactly the call sites of this component for that capability -- nothing else to find or
 * untangle, by design.
 *
 * `detail` names the specific invented mechanism (e.g. the invented mock route), so the marker is honest about
 * WHAT is not wired, not just that something is not wired.
 */
export function PreBackendBanner({ detail }: { detail?: string }) {
  return (
    <div
      role="note"
      aria-label="Pre-backend notice"
      style={{
        border: "3px solid var(--bad-bd)",
        background: "var(--bad-bg)",
        color: "var(--bad-fg)",
        borderRadius: "var(--r-md)",
        padding: "10px 14px",
        marginBottom: 12,
        fontWeight: 700,
        letterSpacing: "0.02em",
        textTransform: "uppercase",
        fontSize: "var(--t-sm)",
      }}
    >
      PRE-BACKEND — NOT WIRED TO A REAL SYSTEM
      {detail ? (
        <span style={{ display: "block", fontWeight: 400, textTransform: "none", letterSpacing: "normal", marginTop: 4 }}>{detail}</span>
      ) : null}
    </div>
  );
}
