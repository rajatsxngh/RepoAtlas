# remeda flow questions

- Written by: Claude (AI draft from reading the source at the pinned commit; needs owner review)
- Reviewed by: pending

## remeda-flow-001

**Question:** When `map(fn)` is called without data, how does it become a one-argument function that `pipe` can recognize as lazy?

**Must cite:**
- `ts:packages/remeda/src/map.ts:map:function`
- `ts:packages/remeda/src/internal/purryWithLazy.ts:purryWithLazy:function`
- `ts:packages/remeda/src/internal/lazyDataLastImplementation.ts:lazyDataLastImplementation:function`
- `ts:packages/remeda/src/internal/dataLastImplementation.ts:dataLastImplementation:function`

**Reference outline:**
1. `map` forwards all its arguments to `purryWithLazy`, together with its data-first implementation, an arity of 2, and its lazy evaluator factory.
2. `purryWithLazy` compares the argument count with the arity; with only the callback it takes the data-last branch and calls `lazyDataLastImplementation`.
3. `lazyDataLastImplementation` asks `dataLastImplementation` for a plain unary function that will call the data-first implementation with the data plus the saved arguments (passing a single saved argument directly instead of spreading).
4. It then attaches the lazy factory as a `lazy` property and the saved arguments as `lazyArgs` on that function and returns it.
5. `pipe` later tells lazy functions apart from plain ones by checking whether the function has a `lazy` property.

## remeda-flow-002

**Question:** In `pipe(array, map(fn), take(n))`, how does `pipe` fuse the two lazy steps and stop iterating once `take` has its `n` items?

**Must cite:**
- `ts:packages/remeda/src/pipe.ts:pipe:function`
- `ts:packages/remeda/src/pipe.ts:isStoppableFrom:function`
- `ts:packages/remeda/src/internal/fusedSequence.ts:buildFusedSequence:function`
- `ts:packages/remeda/src/map.ts:lazyImplementation:function`
- `ts:packages/remeda/src/take.ts:lazyImplementation:function`
- `ts:packages/remeda/src/internal/fusedSequence.ts:runFusedSequence:function`
- `ts:packages/remeda/src/internal/fusedSequence.ts:processItem:function`
- `ts:packages/remeda/src/internal/makeStoppable.ts:lastLazyValue:function`

**Reference outline:**
1. `pipe` sees that `map(fn)` is lazy, that the next function is also lazy, and that `map` is not reducing, so it considers fusing.
2. Because the input is an array, it fuses only if `isStoppableFrom` finds a stoppable lazy function before any non-lazy or reducing one; `take`'s factory carries the `isStoppable` mark (set by `makeStoppable`), so it returns true.
3. `buildFusedSequence` walks the consecutive lazy functions, calling each factory with its `lazyArgs` to get a fresh evaluator (map's and take's `lazyImplementation`), and records one step per function with an item index and, when the evaluator requires it, a data buffer.
4. `runFusedSequence` iterates the input one item at a time, calling `processItem` for each and breaking out of the loop when it reports done.
5. `processItem` passes the item through each step's evaluator in order; take's evaluator decrements its remaining count and, on the last wanted item, calls `lastLazyValue`, which stores the item in the slot and returns the `LAST` control.
6. On `LAST`, `processItem` continues the item through any remaining steps, adds it to the accumulator, and returns true so iteration stops.
7. Back in `pipe`, the function index skips past the fused steps and, since `take` is not reducing, the accumulated array becomes the output.

## remeda-flow-003

**Question:** How does `sortBy` tell a data-first call from a data-last call and turn several order rules into a single comparator?

**Must cite:**
- `ts:packages/remeda/src/sortBy.ts:sortBy:function`
- `ts:packages/remeda/src/internal/purryOrderRules.ts:purryOrderRules:function`
- `ts:packages/remeda/src/internal/purryOrderRules.ts:isOrderRule:function`
- `ts:packages/remeda/src/internal/purryOrderRules.ts:isProjection:function`
- `ts:packages/remeda/src/internal/purryOrderRules.ts:orderRuleComparer:function`
- `ts:packages/remeda/src/sortBy.ts:sortByImplementation:function`

**Reference outline:**
1. `sortBy` hands its implementation and all arguments to `purryOrderRules`.
2. `purryOrderRules` splits off the first argument and checks it with `isOrderRule`, which accepts either a projection (per `isProjection`, a function taking at most one parameter) or a two-element tuple of a projection and a known direction key (`asc` or `desc`).
3. If the first argument is not an order rule, it is the data: the remaining rules are turned into a comparator and the implementation runs immediately (data-first).
4. Otherwise all arguments are rules: the comparator is built up front and a function that takes the data later is returned (data-last).
5. `orderRuleComparer` takes the projection and direction from the primary rule (default `asc`), recursively builds a comparer for the remaining rules, and returns a compare function that projects both items and falls through to the next comparer (or 0) on a tie.
6. `sortByImplementation` copies the array and sorts the copy with that comparator, leaving the input unchanged.

## remeda-flow-004

**Question:** With the default trailing timing, how does a call to a `debounce`d function end up invoking the wrapped function after the wait elapses?

**Must cite:**
- `ts:packages/remeda/src/debounce.ts:debounce:function`
- `ts:packages/remeda/src/debounce.ts:debounce.handleDebouncedCall:function`
- `ts:packages/remeda/src/debounce.ts:debounce.handleCoolDownEnd:function`
- `ts:packages/remeda/src/debounce.ts:debounce.handleInvoke:function`

**Reference outline:**
1. `debounce` validates that `maxWaitMs` is not less than `waitMs` and returns an object whose `call` closes over the cool-down and max-wait timer IDs, the latest arguments, and the last result.
2. On `call` with trailing timing, `handleDebouncedCall` saves the arguments as the latest call arguments and, if `maxWaitMs` is set and no max-wait timer is running, starts one that will run `handleInvoke`.
3. `call` clears any running cool-down timer and starts a new one for `waitMs` (falling back to `maxWaitMs`, then 0) pointing at `handleCoolDownEnd`, then returns the cached result from the previous invocation.
4. When the cool-down timer fires, `handleCoolDownEnd` clears the timer and, if latest arguments are stored, calls `handleInvoke`.
5. `handleInvoke` clears the max-wait timer, takes and clears the stored arguments, calls the wrapped function with them, and caches its return value as the new result.
