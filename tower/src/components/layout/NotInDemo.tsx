import Link from "next/link";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";

/**
 * K8-K12 preview gate (PARTF_K7_K12_MOCK_CONTRACT.md; see the incident this closes). Shown in place of a
 * gated screen's real content when NEXT_PUBLIC_DQ_PREVIEW_K7_K12 is OFF -- which is always true on the live
 * demo path (run_demo.ps1 never sets it). Deliberately calm, not the red PreBackendBanner: that banner is for
 * the author's own private preview, and showing it here (reachable with the flag off) would defeat the point
 * of hiding this screen from a live audience.
 */
export function NotInDemo({ title }: { title: string }) {
  return (
    <>
      <PageHeader title={title} />
      <PageBody>
        <div className="empty" role="note">
          <h2>This screen isn&rsquo;t part of the current demo.</h2>
          <p style={{ margin: 0 }}>
            <Link href="/">Back to the decision queue</Link>
          </p>
        </div>
      </PageBody>
    </>
  );
}
