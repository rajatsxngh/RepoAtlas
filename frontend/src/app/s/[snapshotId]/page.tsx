import { notFound } from "next/navigation";
import { SnapshotPage } from "@/components/workspace/SnapshotPage";
import { parseSnapshotId } from "@/lib/snapshotId";

export default async function Page({ params }: { params: Promise<{ snapshotId: string }> }) {
  const { snapshotId } = await params;
  const id = parseSnapshotId(snapshotId);
  if (id === null) notFound();
  return <SnapshotPage snapshotId={id} />;
}
