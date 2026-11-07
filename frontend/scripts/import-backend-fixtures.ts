/**
 * Copies the backend's golden fixture graphs (and the fixture repos' file text) into mock-mode
 * fixtures: `src/lib/api/mock/fixtures/data/<name>.json`. Run after the golden graphs change:
 *
 *   npx tsx scripts/import-backend-fixtures.ts
 */

import { existsSync, readFileSync, writeFileSync } from "node:fs";

const FIXTURES = new URL("../../backend/tests/fixtures/", import.meta.url);
const OUT = new URL("../src/lib/api/mock/fixtures/data/", import.meta.url);

interface GoldenNode {
  key: string;
  kind: string;
  path: string;
  meta: Record<string, unknown>;
}

for (const name of ["py_small", "ts_small"]) {
  const golden = JSON.parse(readFileSync(new URL(`golden/${name}.graph.json`, FIXTURES), "utf8"));
  const nodes = Object.values(golden.graph.nodes as Record<string, GoldenNode>);
  const files: Record<string, string> = {};
  for (const node of nodes) {
    if (node.kind !== "file" || node.meta.skip_reason) continue;
    const source = new URL(`repos/${name}/${node.path}`, FIXTURES);
    if (existsSync(source)) files[node.path] = readFileSync(source, "utf8");
  }
  const data = { name, digest: golden.digest, nodes, edges: golden.graph.edges, files };
  writeFileSync(new URL(`${name}.json`, OUT), `${JSON.stringify(data)}\n`);
  console.log(
    `${name}: ${nodes.length} nodes, ${data.edges.length} edges, ${Object.keys(files).length} files`,
  );
}
