"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";

/** Load data with the in-memory session; optional polling. 401 signs the reviewer out. */
export function useLoad<T>(fn: (() => Promise<T>) | null, deps: unknown[], pollMs?: number) {
  const { signOut } = useAuth();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async (): Promise<T | null> => {
    const f = fnRef.current;
    if (!f) return null;
    setLoading(true);
    try {
      const d = await f();
      setData(d);
      setError(null);
      return d;
    } catch (e) {
      const ae = e instanceof ApiError ? e : new ApiError(0, { detail: String(e) });
      if (ae.status === 401) signOut();
      setError(ae);
      return null;
    } finally {
      setLoading(false);
    }
  }, [signOut]);

  useEffect(() => {
    void reload();
    if (!pollMs) return;
    const id = setInterval(() => void reload(), pollMs);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, pollMs, ...deps]);

  return { data, error, loading, reload, setData };
}
