"use client";
import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";
import { IDP_BASE, MOCK } from "@/lib/config";
import { ROSTER } from "@/lib/mock/fixtures";

export const LIVE_DOWN =
  "The live sign-in service isn't running. For sample data, start the Tower with npm run dev:mock (or start the full demo).";

export function SignIn() {
  const { signIn } = useAuth();
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go(u: string) {
    setBusy(true);
    setErr(null);
    try {
      await signIn(u.trim());
    } catch (e) {
      // Live mode with no IdP / API listening: say what to do, not "HTTP 0: Network error".
      if (!MOCK && e instanceof ApiError && e.status === 0) setErr(LIVE_DOWN);
      else setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="signin-h" style={{ maxWidth: 520 }}>
      <h2 id="signin-h">Sign in (T1 dev login)</h2>
      <p className="muted">
        Tokens are held in memory for this tab only. Reloading the page signs you out.
        {MOCK ? " Mock mode: the IdP is simulated in the browser." : ` IdP: ${IDP_BASE}/token`}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (username.trim()) void go(username);
        }}
      >
        <label htmlFor="username">Username</label>
        <div className="row">
          <input id="username" type="text" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} style={{ flex: 1 }} />
          <button className="primary" type="submit" disabled={busy || !username.trim()}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </form>
      {MOCK ? (
        <div style={{ marginTop: 12 }}>
          <p className="hint">Mock roster. Mock state lives in this tab: to act as the checker, sign out and sign in as a different user here (a page reload resets the fixtures).</p>
          <div className="row">
            {ROSTER.map((u) => (
              <button key={u.username} type="button" onClick={() => void go(u.username)} disabled={busy}>
                {u.username}
              </button>
            ))}
          </div>
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

/** Renders children only with a session; otherwise the inline sign-in, so deep links keep working. */
export function Gate({ children }: { children: React.ReactNode }) {
  const { session } = useAuth();
  return session ? <>{children}</> : <SignIn />;
}
