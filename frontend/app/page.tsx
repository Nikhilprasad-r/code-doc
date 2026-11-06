import { Suspense } from "react";

import { Workspace } from "@/components/Workspace";

export default function Home() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted">Loading…</div>}>
      <Workspace />
    </Suspense>
  );
}
