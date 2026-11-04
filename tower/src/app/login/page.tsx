"use client";
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { SignIn } from "@/components/SignIn";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

export default function LoginPage() {
  const { session } = useAuth();
  return (
    <>
      <PageHeader title={session ? "Signed in" : "Sign in"} />
      <PageBody>
        <div className="loginwrap">
          {session ? (
            <section className="panel">
              <h2>Signed in as {session.me.name}</h2>
              <p>
                Duties: {session.me.duties.join(", ") || "none"} · Authorities: {session.me.authorities.join(", ") || "none"}
              </p>
              <Link href="/">Go to the Decision queue</Link>
            </section>
          ) : (
            <SignIn />
          )}
        </div>
      </PageBody>
    </>
  );
}
