"use client";

import { MapWorkspace } from "./MapWorkspace";
import { SnapshotGate } from "./SnapshotGate";

export function SnapshotPage({ snapshotId }: { snapshotId: number }) {
  return (
    <SnapshotGate snapshotId={snapshotId}>
      {(snapshot) => <MapWorkspace snapshot={snapshot} />}
    </SnapshotGate>
  );
}
