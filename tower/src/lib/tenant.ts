"use client";
import { useEffect, useState } from "react";
import { makeClient } from "./api";
import type { Tenant } from "./types";

/** The tenant is public and static for a deployment: fetched once per tab and shared. */
let cached: Promise<Tenant> | null = null;

export function useTenant(): { tenant: Tenant | null; error: string | null } {
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    cached = cached ?? makeClient(null).tenant();
    cached.then(
      (t) => live && setTenant(t),
      (e: unknown) => {
        cached = null;
        if (live) setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      live = false;
    };
  }, []);
  return { tenant, error };
}

export function orgLabel(t: Tenant | null, code: string | null | undefined): string | null {
  if (!code) return null;
  return t?.organisations?.[code] ?? code;
}
