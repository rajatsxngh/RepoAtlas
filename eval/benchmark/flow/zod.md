# zod flow questions

- Written by: Claude (AI draft from reading the source at the pinned commit; needs owner review)
- Reviewed by: rajatsinghh (delegated: Claude checked each symbol against the code and applied the pass-through rule; not read line by line by the owner)

## zod-flow-001

**Question:** When you call `z.string().min(5).parse("abc")`, how does Zod build the schema, run the type check and the min-length check, and turn the failure into a thrown error with a message?

**Must cite:**
- `ts:packages/zod/src/v4/core/api.ts:_string:function`
- `ts:packages/zod/src/v4/core/api.ts:_minLength:function`
- `ts:packages/zod/src/v4/core/checks.ts:$ZodCheckMinLength:function`
- `ts:packages/zod/src/v4/core/parse.ts:_parse.fn:function`
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodString:function`
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodType.runChecks:function`
- `ts:packages/zod/src/v4/core/util.ts:finalizeIssue:function`

**Reference outline:**
1. The classic `string()` delegates to the core `_string` helper, which constructs a `ZodString` from a `{ type: "string" }` def; the classic `ZodType` initializer also registers the English locale as the default error map if none is set.
2. `.min(5)` adds a check made by `_minLength` (re-exported as `minLength`), which builds a `$ZodCheckMinLength`; adding a check clones the schema with the check appended to `def.checks`, and the check's on-attach hook records the minimum in the schema's bag.
3. Because the schema now has checks, the core `$ZodType` initializer installs a `run` that first calls the type's own `parse`, then runs the checks.
4. The classic `.parse` method calls the core parser built by `_parse(ZodRealError)`; its inner function makes a sync context and calls `schema._zod.run` with a fresh `{ value, issues: [] }` payload.
5. The `parse` set up by `$ZodString` accepts the value because it is a string (it pushes an `invalid_type` issue only for non-strings).
6. `runChecks` loops over the checks; the min-length check's `when` guard passes for a value with a length, and its `check` pushes a `too_small` issue because "abc" is shorter than 5; the issue is stamped with the owning schema.
7. Back in the parser, issues are present, so each raw issue goes through `finalizeIssue`, which picks the message (issue message, then schema/check error maps, the per-parse map, the global custom map, then the locale map, falling back to "Invalid input") and strips internal fields.
8. The parser builds a `ZodRealError` (an `Error` subclass) from the final issues, restores its stack trace, and throws it.

## zod-flow-002

**Question:** How does a `z.strictObject({...})` schema reject an input that has an extra key, and where does the "Unrecognized key" message come from?

**Must cite:**
- `ts:packages/zod/src/v4/classic/schemas.ts:strictObject:function`
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodObjectJIT:function`
- `ts:packages/zod/src/v4/core/schemas.ts:handleCatchall:function`
- `ts:packages/zod/src/v4/core/util.ts:finalizeIssue:function`
- `ts:packages/zod/src/v4/locales/en.ts:error:function`

**Reference outline:**
1. `strictObject` constructs a `ZodObject` whose def has the given shape and a `never()` schema as its `catchall`.
2. The classic `ZodObject` initializer sets up the core `$ZodObjectJIT` (which itself initializes `$ZodObject` and keeps its interpreted `parse` as a fallback) and then the classic `ZodType` layer.
3. At parse time the JIT object's `parse` rejects non-objects, then validates the declared keys, either through a generated fast path (sync parse with eval allowed) or through the interpreted `$ZodObject` parse; because a catchall is set, both routes end in `handleCatchall`.
4. `handleCatchall` walks the input's keys, skips declared ones, and, since the catchall's type is `never`, collects each undeclared key instead of parsing it.
5. If any were collected it pushes one `unrecognized_keys` issue listing them, marked `continue: true` so the issue does not abort the remaining checks.
6. When the parser finalizes the issues, `finalizeIssue` finds no message or custom map and falls through to the global locale map, the English one installed by default.
7. The English error map's `unrecognized_keys` case produces "Unrecognized key" (plural when there are several) followed by the joined key names.

## zod-flow-003

**Question:** How does `z.toJSONSchema(schema)` turn a `z.object({...})` schema into a JSON Schema document, and where do its properties, required list, and registered metadata get filled in?

**Must cite:**
- `ts:packages/zod/src/v4/core/json-schema-processors.ts:toJSONSchema:function`
- `ts:packages/zod/src/v4/core/to-json-schema.ts:initializeContext:function`
- `ts:packages/zod/src/v4/core/to-json-schema.ts:process:function`
- `ts:packages/zod/src/v4/core/json-schema-processors.ts:objectProcessor:function`
- `ts:packages/zod/src/v4/core/registries.ts:$ZodRegistry.get:method`
- `ts:packages/zod/src/v4/core/to-json-schema.ts:extractDefs:function`
- `ts:packages/zod/src/v4/core/to-json-schema.ts:finalize:function`

**Reference outline:**
1. The public `toJSONSchema` (exported from `json-schema-processors.ts`) takes the single-schema branch: it builds a context with `initializeContext`, passing the table of per-type processors.
2. `initializeContext` normalizes the target (default draft 2020-12), defaults the metadata registry to the global registry, and sets the `io` mode (default output), cycle and reuse policies, and an empty `seen` map.
3. `process` records the schema in `seen`; if the schema has no custom override it calls the schema's own `processJSONSchema` hook, which the classic `ZodObject` wires to `objectProcessor` (otherwise it looks up a processor by def type).
4. `objectProcessor` sets `type: "object"`, calls `process` on every shape entry to fill `properties`, computes `required` from each field's optionality for the chosen `io` mode, and sets `additionalProperties` from the catchall (false for strict objects, and for plain objects in output mode).
5. Back in `process`, the schema's metadata is read with the registry's `get` (which also inherits a parent's metadata, minus its `id`) and merged into the JSON output.
6. `extractDefs` walks the seen schemas, rejects duplicate ids, and decides which schemas become definitions referenced by `$ref`: those with an `id` in their metadata, those on a cycle, and reused ones only when `reused` is set to `"ref"` (the default is inline).
7. `finalize` flattens references, runs the user's `override` callback, adds the `$schema` URL for the target, copies the root schema, and attaches the collected definitions.

## zod-flow-004

**Question:** When a classic schema has `.transform(fn)` and you parse a value, how is the value validated first and then handed to `fn`, and what stops `fn` from running on invalid input?

**Must cite:**
- `ts:packages/zod/src/v4/classic/schemas.ts:transform:function`
- `ts:packages/zod/src/v4/classic/schemas.ts:pipe:function`
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodPipe:function`
- `ts:packages/zod/src/v4/core/schemas.ts:handlePipeResult:function`
- `ts:packages/zod/src/v4/classic/schemas.ts:ZodTransform:function`

**Reference outline:**
1. The `.transform(fn)` method on classic `ZodType` builds `pipe(this, transform(fn))`: `transform` creates a `ZodTransform` whose def holds `fn`, and `pipe` creates a `ZodPipe` with the original schema as `in` and the transform as `out`.
2. The `parse` installed by `$ZodPipe` (forward direction) runs the `in` schema on the payload first.
3. `handlePipeResult` stops the pipe if the left result has any issue other than `unrecognized_keys`: it marks the payload aborted and returns it, so `fn` never runs.
4. Otherwise it runs the `out` schema on a payload carrying the validated value and the existing issues.
5. The classic `ZodTransform` replaces the core transform's `parse`: it throws for encode (backward) direction, attaches an `addIssue` helper to the payload, calls `fn` with the value and payload, and stores the result (awaiting it if `fn` returns a promise) as the new value.
