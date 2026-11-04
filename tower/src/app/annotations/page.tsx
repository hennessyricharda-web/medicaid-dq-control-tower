"use client";
/**
 * K11 (PARTF_K7_K12_MOCK_CONTRACT.md): annotation queue. PRE-BACKEND -- calls the INVENTED `GET
 * /annotation-queue` (`client.annotationQueue`), which has no real counterpart. Every row names a REAL entity key
 * already used elsewhere in this codebase (a decision unit's `unit_key`, linking to the real `/units/[unitKey]`
 * card where the K11 feedback control lives; or a feed item, linking to Reconciliation) -- never a fabricated id.
 */
import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useLoad } from "@/lib/useLoad";
import { SignIn } from "@/components/SignIn";
import { ErrorNote } from "@/components/ErrorNote";
import { ToneChip } from "@/components/Chip";
import { fmtTime } from "@/lib/format";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { EmptyState } from "@/components/layout/EmptyState";
import { PreBackendBanner } from "@/components/layout/PreBackendBanner";
import { NotInDemo } from "@/components/layout/NotInDemo";
import { PREVIEW_K7_K12 } from "@/lib/config";

function AnnotationQueueView() {
  const { client } = useAuth();
  const { data, error, loading } = useLoad(() => client.annotationQueue(), [client]);
  if (error) return <ErrorNote error={error} what="the annotation queue" />;
  if (loading && !data) return <p className="hint">Loading&hellip;</p>;
  const items = data?.items ?? [];
  return (
    <section className="panel" aria-labelledby="aq-h">
      <h2 id="aq-h">
        Awaiting annotation <span className="sub">{items.length} item(s)</span>
      </h2>
      {items.length ? (
        <div className="tablewrap">
          <table>
            <caption className="skip">Decision units and feed items awaiting human annotation or feedback</caption>
            <thead>
              <tr>
                <th scope="col">Subject</th>
                <th scope="col">Kind</th>
                <th scope="col">Status</th>
                <th scope="col">At</th>
                <th scope="col" aria-label="open" />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.subject}>
                  <td className="mono" style={{ whiteSpace: "normal" }}>
                    {it.label}
                  </td>
                  <td>{it.kind === "UNIT" ? "Decision unit" : "Feed item"}</td>
                  <td>
                    {it.status === "FEEDBACK_RECORDED" ? (
                      <ToneChip tone="ok" label="feedback recorded" />
                    ) : (
                      <ToneChip tone="neutral" label="open" />
                    )}
                  </td>
                  <td>{fmtTime(it.at)}</td>
                  <td>
                    {it.kind === "UNIT" ? (
                      <Link href={`/units/${encodeURIComponent(it.subject)}`}>Open card</Link>
                    ) : (
                      <Link href="/reconciliation">Open reconciliation</Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState title="Nothing awaiting annotation." line="No open decision unit or feed item in this tab's queue." />
      )}
    </section>
  );
}

export default function Page() {
  const { session } = useAuth();
  // K8-K12 preview gate: OFF by default; run_demo.ps1 never sets NEXT_PUBLIC_DQ_PREVIEW_K7_K12. Self-guarded
  // here (not just hidden from the nav rail) so a guessed /annotations URL shows nothing live during the demo
  // -- checked before the sign-in gate below, so it never even reveals that a sign-in form exists here.
  if (!PREVIEW_K7_K12) return <NotInDemo title="Annotation queue" />;
  if (!session)
    return (
      <>
        <PageHeader title="Annotation queue" />
        <PageBody>
          <SignIn />
        </PageBody>
      </>
    );
  return (
    <>
      <PageHeader title="Annotation queue" sub="decision units and feed items awaiting human feedback" />
      <PageBody>
        <PreBackendBanner detail="Invented mock route (GET /annotation-queue); no real backend route exists for this yet. Subjects are real entity keys; the queue mechanism itself is not." />
        <AnnotationQueueView />
      </PageBody>
    </>
  );
}
