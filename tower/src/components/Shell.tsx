"use client";
/** Root chrome: the in-memory session, then the Part F app shell (components/layout/AppShell.tsx). */
import { AuthProvider } from "@/lib/auth";
import { AppShell } from "./layout/AppShell";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  );
}
