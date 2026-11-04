"use client";

/**
 * Bearer token is held in React state only — never localStorage/sessionStorage/cookies.
 * A full page reload therefore signs the reviewer out; that is intentional.
 */
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { makeClient, idpToken, type Client } from "./api";
import type { Me } from "./types";

interface Session {
  token: string;
  username: string;
  me: Me;
}

interface AuthCtx {
  session: Session | null;
  client: Client;
  signIn: (username: string) => Promise<void>;
  signOut: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);

  const signIn = useCallback(async (username: string) => {
    const token = await idpToken(username);
    const me = await makeClient(token).me();
    setSession({ token, username, me });
  }, []);

  const signOut = useCallback(() => setSession(null), []);
  const client = useMemo(() => makeClient(session?.token ?? null), [session?.token]);

  const value = useMemo(() => ({ session, client, signIn, signOut }), [session, client, signIn, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
