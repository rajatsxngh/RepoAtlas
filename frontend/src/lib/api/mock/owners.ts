/** Made-up but stable file owners for mock fixtures (the golden graphs carry no history). */

import type { Owner } from "../types";

const AUTHORS = [
  "Avery Chen",
  "Sam Okafor",
  "Riya Patel",
  "Jordan Lee",
  "Noor Haddad",
  "Mateo Rossi",
  "Kai Nakamura",
  "Elena Petrova",
];

/** FNV-1a, enough to spread paths over authors deterministically. */
export function hash(text: string): number {
  let value = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    value ^= text.charCodeAt(i);
    value = Math.imul(value, 0x01000193) >>> 0;
  }
  return value;
}

function hex(seed: number): string {
  let out = "";
  let value = seed || 1;
  while (out.length < 64) {
    value = Math.imul(value ^ (value >>> 15), 0x2c1b3c6d) >>> 0;
    out += value.toString(16).padStart(8, "0");
  }
  return out.slice(0, 64);
}

export function syntheticOwners(path: string, authors: readonly string[] = AUTHORS): Owner[] {
  const seed = hash(path);
  const count = 1 + (seed % Math.min(4, authors.length));
  const picked: { name: string; commits: number }[] = [];
  for (let i = 0; i < count; i += 1) {
    const name = authors[(seed + i * 3) % authors.length]!;
    if (picked.some((owner) => owner.name === name)) continue;
    picked.push({ name, commits: 1 + ((seed >>> (i * 4)) % 23) * (count - i) });
  }
  const total = picked.reduce((sum, owner) => sum + owner.commits, 0);
  return picked
    .sort((a, b) => b.commits - a.commits || (a.name < b.name ? -1 : 1))
    .map((owner, index) => ({
      author_name: owner.name,
      author_hash: hex(hash(owner.name)),
      commits: owner.commits,
      last_commit_at: new Date(Date.UTC(2026, 9, 28 - index * 9 - (seed % 7))).toISOString(),
      share: Math.round((owner.commits / total) * 10_000) / 10_000,
    }));
}
