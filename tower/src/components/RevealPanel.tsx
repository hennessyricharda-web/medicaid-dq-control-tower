"use client";
/**
 * REVEAL (DESIGN §8): creates a short-lived, authority-scoped reveal view in the data plane for the
 * reviewer's own DP identity. The Tower shows only the returned reference and expiry — never values.
 */
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtTime } from "@/lib/format";
import type { DecisionCard, RevealResponse } from "@/lib/types";

const PURPOSE_MIN = 10; // UI guard only; the spine sets no minimum for `purpose`

export function RevealPanel({ card }: { card: DecisionCard }) {
  const { client } = useAuth();
  const [purpose, setPurpose] = useState("");
  const [res, setRes] = useState<RevealResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!res) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [res]);

  async function reveal() {
    setBusy(true);
    setErr(null);
    try {
      const r = await client.reveal(card.unit_key, purpose.trim());
      // Keep only the two spine fields; anything else in the response is ignored, never rendered.
      setRes({ reveal_ref: String(r.reveal_ref), expires_at: String(r.expires_at) });
      setNow(Date.now());
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      setErr(ae.status === 403 ? `Not permitted: ${ae.message}` : ae.message);
    } finally {
      setBusy(false);
    }
  }

  const exp = res ? Date.parse(res.expires_at) : NaN;
  const left = Number.isNaN(exp) ? null : Math.max(0, Math.round((exp - now) / 1000));

  return (
    <section className="panel" aria-labelledby="reveal-h">
      <h2 id="reveal-h">Reveal records</h2>
      <p className="hint">
        Creates a short-lived reveal view in the data plane for your own identity. The Tower never receives or shows record values. The request is logged.
      </p>
      <label htmlFor="reveal-purpose">Purpose</label>
      <input id="reveal-purpose" type="text" value={purpose} onChange={(e) => setPurpose(e.target.value)} autoComplete="off" />
      <div className="row" style={{ marginTop: 8 }}>
        <button type="button" onClick={() => void reveal()} disabled={busy || purpose.trim().length < PURPOSE_MIN}>
          {busy ? "Requesting…" : "REVEAL"}
        </button>
        {purpose.trim().length < PURPOSE_MIN ? <span className="hint">State a purpose ({PURPOSE_MIN}+ characters).</span> : null}
      </div>
      {res ? (
        <div className="banner t-info" style={{ marginTop: 10 }} aria-live="polite">
          <dl className="kv">
            <dt>Reveal ref</dt>
            <dd>
              <code>{res.reveal_ref}</code>
            </dd>
            <dt>Expires</dt>
            <dd>
              {fmtTime(res.expires_at)}
              {left !== null ? ` (${left > 0 ? `${Math.floor(left / 60)}m ${left % 60}s left` : "expired"})` : ""}
            </dd>
          </dl>
          <p className="hint">Query this view in the data plane with your own credentials.</p>
        </div>
      ) : null}
      {err ? (
        <p role="alert" className="err">
          {err}
        </p>
      ) : null}
    </section>
  );
}
