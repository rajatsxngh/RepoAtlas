/** Names for the API's models, from the generated OpenAPI types (`schema.d.ts`). */

import type { components, paths } from "./schema";

type Schemas = components["schemas"];

export type ApiNode = Schemas["Node"];
export type NodeKind = Schemas["NodeKind"];
export type EdgeKind = Schemas["EdgeKind"];
export type Provenance = Schemas["Provenance"];
export type Range = Schemas["Range"];
export type Edge = Schemas["Edge"];
export type AggregatedEdge = Schemas["AggregatedEdge"];
export type EdgeLinks = Schemas["EdgeLinks"];
export type Overview = Schemas["Overview"];
export type Ranked = Schemas["Ranked"];
export type Subgraph = Schemas["Subgraph"];
export type SubgraphLevel = Subgraph["level"];
export type NodeDetail = Schemas["NodeDetail"];
export type Owner = Schemas["Owner"];
export type Degree = Schemas["Degree"];
export type Neighborhood = Schemas["Neighborhood"];
export type Direction = Neighborhood["direction"];
export type Hop = Schemas["Hop"];
export type GraphPath = Schemas["GraphPath"];
export type CodeSlice = Schemas["CodeSlice"];
export type SearchResults = Schemas["SearchResults"];
export type SymbolMatch = Schemas["SymbolMatch"];
export type SnapshotView = Schemas["SnapshotView"];
export type SnapshotStatus = SnapshotView["status"];
export type RepoInfo = Schemas["RepoInfo"];
export type JobView = Schemas["JobView"];
export type JobStatus = JobView["status"];
export type BuildAccepted = Schemas["BuildAccepted"];
export type ErrorCode = Schemas["ErrorCode"];
export type AskRequest = Schemas["AskRequest"];
export type AnswerView = Schemas["AnswerView"];
export type AnswerMetrics = Schemas["AnswerMetrics"];
export type Claim = Schemas["Claim"];
export type Citation = Schemas["Citation"];
export type EvidenceRef = Schemas["EvidenceRef"];
export type RefusalKind = NonNullable<AnswerView["refusal_kind"]>;
export type QaRunView = Schemas["QaRunView"];
export type TraceView = Schemas["TraceView"];
export type SpanView = Schemas["SpanView"];
export type ImpactRequest = Schemas["ImpactRequest"];
export type ImpactResponse = Schemas["ImpactResponse"];
export type ImpactItem = Schemas["ImpactItem"];
export type ImpactGroup = ImpactItem["group"];
export type UnknownSite = Schemas["UnknownSite"];
export type PathStep = Schemas["PathStep"];
export type ImpactParams = Schemas["ImpactParams"];

/** The steps `POST /snapshots/{id}/ask` reports in its `status` events, in order. */
export type AskStep = "retrieving" | "answering" | "validating";
export const ASK_STEPS: readonly AskStep[] = ["retrieving", "answering", "validating"];

/** The `done` event: where the run and its trace are stored. */
export interface AskDone {
  run_id: number;
  trace_id: string | null;
}
export type ErrorResponse = Schemas["ErrorResponse"];

export const NODE_KINDS: readonly NodeKind[] = [
  "dir",
  "file",
  "class",
  "function",
  "method",
  "route",
  "test",
];
export const EDGE_KINDS: readonly EdgeKind[] = [
  "contains",
  "imports",
  "calls",
  "inherits",
  "tested_by",
  "routes_to",
];

type Get<P extends keyof paths> = paths[P] extends { get: infer Op } ? Op : never;
type QueryOf<Op> = Op extends { parameters: { query?: infer Q } } ? NonNullable<Q> : never;
type OkOf<Op> = Op extends { responses: { 200: { content: { "application/json": infer B } } } }
  ? B
  : never;

/** Query parameters and 200 bodies of the read endpoints, straight from the spec. */
export type SubgraphQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/subgraph">>;
export type NeighborsQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/nodes/{key}/neighbors">>;
export type LocateQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/locate">>;
export type SearchQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/search">>;
export type PathQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/path">>;
export type CodeQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/code">>;
export type EdgeLinksQuery = QueryOf<Get<"/api/v1/snapshots/{snapshot_id}/links">>;

export type SubgraphBody = OkOf<Get<"/api/v1/snapshots/{snapshot_id}/subgraph">>;
export type LocateBody = OkOf<Get<"/api/v1/snapshots/{snapshot_id}/locate">>;
