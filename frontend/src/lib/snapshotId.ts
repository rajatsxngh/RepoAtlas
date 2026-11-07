/** Snapshot IDs in URLs: positive integers without leading zeros, small enough to be exact. */
export function parseSnapshotId(raw: string): number | null {
  if (!/^[1-9]\d{0,15}$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}
