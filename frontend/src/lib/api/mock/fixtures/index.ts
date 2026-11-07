/** The snapshots mock mode serves. Data loads on first use, so fixtures cost nothing until read. */

import type { ApiNode, Edge } from "../../types";
import type { FixtureData } from "../graph";
import { syntheticOwners } from "../owners";

export interface MockFixture {
  id: number;
  owner: string;
  name: string;
  commit: string;
  /** Every build of this fixture fails (for error states). */
  failsToBuild?: boolean;
  load: () => Promise<FixtureData>;
}

interface GoldenFixture {
  nodes: ApiNode[];
  edges: Edge[];
  files: Record<string, string>;
}

function fromGolden(data: GoldenFixture): FixtureData {
  return { ...data, owners: (path) => syntheticOwners(path) };
}

export const FIXTURE_OWNER = "repoatlas-fixtures";

export const MOCK_FIXTURES: readonly MockFixture[] = [
  {
    id: 1,
    owner: FIXTURE_OWNER,
    name: "py-small",
    commit: "3f9d2c71a8b04e6f95d1c2b7e8a0f4c3d2e1b0a9",
    load: async () => fromGolden((await import("./data/py_small.json")).default as GoldenFixture),
  },
  {
    id: 2,
    owner: FIXTURE_OWNER,
    name: "ts-small",
    commit: "8c1e5b2f7a3d9046e1b8c5d2a7f3e9b0c4d6a1e2",
    load: async () => fromGolden((await import("./data/ts_small.json")).default as GoldenFixture),
  },
  {
    id: 3,
    owner: FIXTURE_OWNER,
    name: "xss",
    commit: "0badc0de0badc0de0badc0de0badc0de0badc0de",
    load: async () => (await import("./xss")).xssFixture(),
  },
  {
    id: 4,
    owner: FIXTURE_OWNER,
    name: "synthetic",
    commit: "5e7a1c9b3d2f4e6a8c0b1d3f5a7c9e1b2d4f6a8c",
    load: async () => (await import("./synthetic")).syntheticGraph(),
  },
  {
    id: 5,
    owner: FIXTURE_OWNER,
    name: "broken",
    commit: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    failsToBuild: true,
    load: async () => fromGolden((await import("./data/py_small.json")).default as GoldenFixture),
  },
];

export function fixtureById(id: number): MockFixture | undefined {
  return MOCK_FIXTURES.find((fixture) => fixture.id === id);
}

export function fixtureByName(name: string): MockFixture | undefined {
  const wanted = name.toLowerCase();
  return MOCK_FIXTURES.find((fixture) => fixture.name === wanted);
}
