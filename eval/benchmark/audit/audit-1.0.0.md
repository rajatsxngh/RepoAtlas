# Benchmark 1.0.0 audit

A seeded sample (`--sample 0.1 --seed 1`) of 11 of the 110 generated questions (flow questions are reviewed separately). Check each one against the code on GitHub at the pinned commit; links point there. When one is wrong, fix the generator, regenerate, and audit again.

Audited 2026-11-12 by Claude against the code at the pinned commits (the owner delegated the audit). Round 3, after the generator fixes listed in the benchmark README.

| Question | Type | Verdict |
|---|---|---|
| click-impact-002 | impact | pass (static set is the SCIP-visible callers; all 59 real failures come through untyped fixtures) |
| click-impact-004 | impact | pass (same limitation: 938 of 980 real failures are outside the static set) |
| click-unanswerable-001 | unanswerable | pass (git grep: no match) |
| httpx-callers-005 | callers | pass |
| httpx-multihop-001 | multihop | pass |
| remeda-lookup-002 | lookup | pass |
| requests-multihop-002 | multihop | pass (the 4 tests that call PreparedRequest().prepare) |
| requests-unanswerable-002 | unanswerable | pass |
| zod-callers-002 | callers | pass |
| zod-callers-004 | callers | pass (git grep finds exactly these 9 calls) |
| zod-lookup-002 | lookup | pass |

## click-impact-002 (impact, dev, sandbox)

**Question:** What breaks if I change `Group.resolve_command` (defined in `src/click/core.py`)? List the functions, methods, and tests that depend on it.

**Notes:** static: callers up to depth 3 (8). real: 59 confirmed failing tests from the sandbox mutation (raise, seed 7); 0 unmapped; 59 are not in the static set (SCIP does not see references through untyped values such as pytest fixtures).

**Target:** `py:src/click/core.py:Group.resolve_command:method` at [src/click/core.py:2088](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2088)

**Static set:** 8 definitions within 3 caller hops. The 5 direct callers, with the reference lines:

- `py:examples/aliases/aliases.py:AliasedGroup.resolve_command:method` at [examples/aliases/aliases.py:70](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/aliases/aliases.py#L70): [examples/aliases/aliases.py:72](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/aliases/aliases.py#L72)
- `py:src/click/core.py:Group.invoke:method` at [src/click/core.py:2020](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2020): [src/click/core.py:2048](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2048), [src/click/core.py:2070](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2070)
- `py:src/click/shell_completion.py:_resolve_context:function` at [src/click/shell_completion.py:691](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L691): [src/click/shell_completion.py:714](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L714), [src/click/shell_completion.py:728](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L728)
- `py:tests/test_commands.py:test_aliased_command_canonical_name.AliasedGroup.resolve_command:method` at [tests/test_commands.py:334](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_commands.py#L334): [tests/test_commands.py:335](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_commands.py#L335)
- `py:tests/typing/typing_aliased_group.py:AliasedGroup.resolve_command:method` at [tests/typing/typing_aliased_group.py:22](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/typing/typing_aliased_group.py#L22): [tests/typing/typing_aliased_group.py:26](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/typing/typing_aliased_group.py#L26)

**Real failing tests** (sandbox mutation): 59, first 10:

- `tests/test_arguments.py::test_nested_subcommand_help` → `py:tests/test_arguments.py:test_nested_subcommand_help:test`
- `tests/test_arguments.py::test_subcommand_help` → `py:tests/test_arguments.py:test_subcommand_help:test`
- `tests/test_basic.py::test_basic_group` → `py:tests/test_basic.py:test_basic_group:test`
- `tests/test_basic.py::test_group_commands_dict` → `py:tests/test_basic.py:test_group_commands_dict:test`
- `tests/test_basic.py::test_group_from_list` → `py:tests/test_basic.py:test_group_from_list:test`
- `tests/test_chain.py::test_args_and_chain` → `py:tests/test_chain.py:test_args_and_chain:test`
- `tests/test_chain.py::test_basic_chaining` → `py:tests/test_chain.py:test_basic_chaining:test`
- `tests/test_chain.py::test_chaining_help[args2-SDIST HELP]` → `py:tests/test_chain.py:test_chaining_help:test`
- `tests/test_chain.py::test_chaining_help[args3-BDIST HELP]` → `py:tests/test_chain.py:test_chaining_help:test`
- `tests/test_chain.py::test_chaining_help[args4-SDIST HELP]` → `py:tests/test_chain.py:test_chaining_help:test`

Every line that mentions `resolve_command` (11):

| Line | Classified as | Text |
|---|---|---|
| [examples/aliases/aliases.py:70](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/aliases/aliases.py#L70) | not counted (check) | `def resolve_command(self, ctx, args):` |
| [examples/aliases/aliases.py:72](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/aliases/aliases.py#L72) | counted: caller `py:examples/aliases/aliases.py:AliasedGroup.resolve_command:method` | `_, cmd, args = super().resolve_command(ctx, args)` |
| [src/click/core.py:2048](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2048) | counted: caller `py:src/click/core.py:Group.invoke:method` | `cmd_name, cmd, args = self.resolve_command(ctx, args)` |
| [src/click/core.py:2070](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2070) | counted: caller `py:src/click/core.py:Group.invoke:method` | `cmd_name, cmd, args = self.resolve_command(ctx, args)` |
| [src/click/core.py:2088](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2088) | definition | `def resolve_command(` |
| [src/click/shell_completion.py:714](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L714) | counted: caller `py:src/click/shell_completion.py:_resolve_context:function` | `name, cmd, args = command.resolve_command(ctx, args)` |
| [src/click/shell_completion.py:728](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L728) | counted: caller `py:src/click/shell_completion.py:_resolve_context:function` | `name, cmd, args = command.resolve_command(ctx, args)` |
| [tests/test_commands.py:334](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_commands.py#L334) | not counted (check) | `def resolve_command(self, ctx, args):` |
| [tests/test_commands.py:335](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_commands.py#L335) | counted: caller `py:tests/test_commands.py:test_aliased_command_canonical_name.AliasedGroup.resolve_command:method` | `_, command, args = super().resolve_command(ctx, args)` |
| [tests/typing/typing_aliased_group.py:22](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/typing/typing_aliased_group.py#L22) | not counted (check) | `def resolve_command(` |
| [tests/typing/typing_aliased_group.py:26](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/typing/typing_aliased_group.py#L26) | counted: caller `py:tests/typing/typing_aliased_group.py:AliasedGroup.resolve_command:method` | `_, cmd, args = super().resolve_command(ctx, args)` |

Check: the direct callers are right (as for callers questions); deeper levels apply the same rule to each caller. Real failures come from the sandbox records.

- [x] Matches the code at the pinned commit
- Auditor notes:

## click-impact-004 (impact, test, sandbox)

**Question:** What breaks if I change `Result.output` (defined in `src/click/testing.py`)? List the functions, methods, and tests that depend on it.

**Notes:** static: callers up to depth 3 (35). real: 980 confirmed failing tests from the sandbox mutation (raise, seed 7); 0 unmapped; 938 are not in the static set (SCIP does not see references through untyped values such as pytest fixtures).

**Target:** `py:src/click/testing.py:Result.output:method` at [src/click/testing.py:283](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L283)

**Static set:** 35 definitions within 3 caller hops. The 35 direct callers, with the reference lines:

- `py:tests/test_options.py:test_flag_value_on_option_with_zero_or_one_args:test` at [tests/test_options.py:3613](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_options.py#L3613): [tests/test_options.py:3640](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_options.py#L3640)
- `py:tests/test_stream_lifecycle.py:test_exception_does_not_corrupt_next_invoke:test` at [tests/test_stream_lifecycle.py:430](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L430): [tests/test_stream_lifecycle.py:446](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L446), [tests/test_stream_lifecycle.py:454](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L454)
- `py:tests/test_stream_lifecycle.py:test_getvalue_after_isolation_exit:test` at [tests/test_stream_lifecycle.py:142](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L142): [tests/test_stream_lifecycle.py:155](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L155), [tests/test_stream_lifecycle.py:156](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L156)
- `py:tests/test_stream_lifecycle.py:test_invoke_with_logger_and_prompt:test` at [tests/test_stream_lifecycle.py:208](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L208): [tests/test_stream_lifecycle.py:221](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L221)
- `py:tests/test_stream_lifecycle.py:test_invoke_with_logger_warning:test` at [tests/test_stream_lifecycle.py:193](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L193): [tests/test_stream_lifecycle.py:205](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L205)
- `py:tests/test_stream_lifecycle.py:test_invoke_with_stream_handler_on_stderr:test` at [tests/test_stream_lifecycle.py:246](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L246): [tests/test_stream_lifecycle.py:262](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L262)
- `py:tests/test_stream_lifecycle.py:test_invoke_with_thread_pool:test` at [tests/test_stream_lifecycle.py:300](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L300): [tests/test_stream_lifecycle.py:313](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L313)
- `py:tests/test_stream_lifecycle.py:test_invoke_with_threads_writing_to_streams:test` at [tests/test_stream_lifecycle.py:316](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L316): [tests/test_stream_lifecycle.py:337](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L337)
- `py:tests/test_stream_lifecycle.py:test_logging_with_cli_log_level:test` at [tests/test_stream_lifecycle.py:267](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L267): [tests/test_stream_lifecycle.py:289](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L289)
- `py:tests/test_stream_lifecycle.py:test_mixed_output_order_preserved:test` at [tests/test_stream_lifecycle.py:413](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L413): [tests/test_stream_lifecycle.py:425](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L425)
- `py:tests/test_stream_lifecycle.py:test_output_isolation_across_invokes:test` at [tests/test_stream_lifecycle.py:382](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L382): [tests/test_stream_lifecycle.py:394](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L394)
- `py:tests/test_stream_lifecycle.py:test_sequential_invokes_with_logging:test` at [tests/test_stream_lifecycle.py:224](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L224): [tests/test_stream_lifecycle.py:243](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L243)
- `py:tests/test_stream_lifecycle.py:test_sequential_threaded_invokes:test` at [tests/test_stream_lifecycle.py:360](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L360): [tests/test_stream_lifecycle.py:374](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L374)
- `py:tests/test_stream_lifecycle.py:test_stress_gc_between_invocations:test` at [tests/test_stream_lifecycle.py:539](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L539): [tests/test_stream_lifecycle.py:550](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_stream_lifecycle.py#L550)
- `py:tests/test_testing.py:test_args:test` at [tests/test_testing.py:395](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L395): [tests/test_testing.py:404](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L404)
- `py:tests/test_testing.py:test_capture_fd_faulthandler:test` at [tests/test_testing.py:725](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L725): [tests/test_testing.py:736](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L736)
- `py:tests/test_testing.py:test_capture_fd_logging_handler:test` at [tests/test_testing.py:679](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L679): [tests/test_testing.py:712](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L712), [tests/test_testing.py:713](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L713), [tests/test_testing.py:718](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L718) (+1 more)
- `py:tests/test_testing.py:test_capture_fd_stderr_separation:test` at [tests/test_testing.py:759](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L759): [tests/test_testing.py:776](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L776), [tests/test_testing.py:777](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L777), [tests/test_testing.py:778](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L778) (+1 more)
- `py:tests/test_testing.py:test_capture_sys_fileno_raises:test` at [tests/test_testing.py:739](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L739): [tests/test_testing.py:754](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L754)
- `py:tests/test_testing.py:test_command_standalone_mode_returns_value:test` at [tests/test_testing.py:418](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L418): [tests/test_testing.py:426](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L426)
- `py:tests/test_testing.py:test_echo_stdin_prompts:test` at [tests/test_testing.py:52](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L52): [tests/test_testing.py:61](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L61), [tests/test_testing.py:70](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L70), [tests/test_testing.py:79](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L79) (+1 more)
- `py:tests/test_testing.py:test_echo_stdin_stream:test` at [tests/test_testing.py:34](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L34): [tests/test_testing.py:49](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L49)
- `py:tests/test_testing.py:test_env:test` at [tests/test_testing.py:340](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L340): [tests/test_testing.py:353](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L353)
- `py:tests/test_testing.py:test_exit_code_and_output_from_sys_exit:test` at [tests/test_testing.py:271](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L271): [tests/test_testing.py:313](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L313), [tests/test_testing.py:317](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L317), [tests/test_testing.py:321](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L321) (+4 more)
- `py:tests/test_testing.py:test_getchar:test` at [tests/test_testing.py:137](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L137): [tests/test_testing.py:145](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L145), [tests/test_testing.py:150](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L150), [tests/test_testing.py:159](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L159) (+1 more)
- `py:tests/test_testing.py:test_pdb_uses_real_streams:test` at [tests/test_testing.py:567](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L567): [tests/test_testing.py:582](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L582)
- `py:tests/test_testing.py:test_prompts:test` at [tests/test_testing.py:115](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L115): [tests/test_testing.py:124](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L124), [tests/test_testing.py:134](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L134)
- `py:tests/test_testing.py:test_runner:test` at [tests/test_testing.py:16](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L16): [tests/test_testing.py:31](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L31)
- `py:tests/test_testing.py:test_runner_with_stream:test` at [tests/test_testing.py:92](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L92): [tests/test_testing.py:107](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L107), [tests/test_testing.py:112](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L112)
- `py:tests/test_testing.py:test_setting_prog_name_in_extra:test` at [tests/test_testing.py:407](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L407): [tests/test_testing.py:415](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L415)
- `py:tests/test_testing.py:test_stderr:test` at [tests/test_testing.py:358](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L358): [tests/test_testing.py:369](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L369), [tests/test_testing.py:380](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L380)
- `py:tests/test_testing.py:test_with_color:test` at [tests/test_testing.py:213](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L213): [tests/test_testing.py:221](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L221), [tests/test_testing.py:225](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L225)
- `py:tests/test_testing.py:test_with_color_but_pause_not_blocking:test` at [tests/test_testing.py:249](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L249): [tests/test_testing.py:257](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L257)
- `py:tests/test_testing.py:test_with_color_errors:test` at [tests/test_testing.py:229](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L229): [tests/test_testing.py:241](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L241), [tests/test_testing.py:245](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L245)
- `py:tests/test_testing.py:test_with_echo_via_pager:test` at [tests/test_testing.py:260](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L260): [tests/test_testing.py:268](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_testing.py#L268)

**Real failing tests** (sandbox mutation): 980, first 10:

- `tests/test_arguments.py::test_argument_help` → `py:tests/test_arguments.py:test_argument_help:test`
- `tests/test_arguments.py::test_argument_help_deprecated_without_help_lists_all` → `py:tests/test_arguments.py:test_argument_help_deprecated_without_help_lists_all:test`
- `tests/test_arguments.py::test_argument_help_empty_string_lists_all` → `py:tests/test_arguments.py:test_argument_help_empty_string_lists_all:test`
- `tests/test_arguments.py::test_argument_help_lists_undocumented_arguments` → `py:tests/test_arguments.py:test_argument_help_lists_undocumented_arguments:test`
- `tests/test_arguments.py::test_argument_help_optional_metavar` → `py:tests/test_arguments.py:test_argument_help_optional_metavar:test`
- `tests/test_arguments.py::test_argument_help_options_only_no_arguments_section` → `py:tests/test_arguments.py:test_argument_help_options_only_no_arguments_section:test`
- `tests/test_arguments.py::test_argument_help_undocumented_arguments_only_no_section` → `py:tests/test_arguments.py:test_argument_help_undocumented_arguments_only_no_section:test`
- `tests/test_arguments.py::test_argument_metavar_marks_optional[kwargs0-FOO]` → `py:tests/test_arguments.py:test_argument_metavar_marks_optional:test`
- `tests/test_arguments.py::test_argument_metavar_marks_optional[kwargs1-FOO]` → `py:tests/test_arguments.py:test_argument_metavar_marks_optional:test`
- `tests/test_arguments.py::test_argument_metavar_marks_optional[kwargs2-[FOO]]` → `py:tests/test_arguments.py:test_argument_metavar_marks_optional:test`

Every line that mentions `output` (566):

| Line | Classified as | Text |
|---|---|---|
| [examples/inout/inout.py:6](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/inout/inout.py#L6) | not counted (check) | `@click.argument("output", type=click.File("wb"))` |
| [examples/inout/inout.py:7](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/inout/inout.py#L7) | not counted (check) | `def cli(input, output):` |
| [examples/inout/inout.py:9](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/inout/inout.py#L9) | not counted (check) | `into a specific file (which could be the standard output as denoted by` |
| [examples/inout/inout.py:29](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/inout/inout.py#L29) | not counted (check) | `output.write(chunk)` |
| [examples/inout/inout.py:30](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/examples/inout/inout.py#L30) | not counted (check) | `output.flush()` |
| [src/click/_termui_impl.py:255](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L255) | not counted (check) | `# Only output the label once if the output is not a TTY.` |
| [src/click/_termui_impl.py:384](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L384) | not counted (check) | `"""Wrap a pager's output stream to strip ANSI styling when colors are` |
| [src/click/_termui_impl.py:415](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L415) | not counted (check) | `# The pager strategy owns the stream's lifecycle. Flush pending output,` |
| [src/click/_termui_impl.py:498](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L498) | not counted (check) | `Yields a writable file-like object which can be used as an output pager.` |
| [src/click/_termui_impl.py:620](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L620) | not counted (check) | `# but less is still alive, paging the output and messing up the terminal.` |
| [src/click/core.py:278](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L278) | not counted (check) | `help output.` |
| [src/click/core.py:501](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L501) | not counted (check) | `#: Controls if styling output is wanted or not.` |
| [src/click/core.py:640](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L640) | not counted (check) | `usage output.` |
| [src/click/decorators.py:449](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/decorators.py#L449) | not counted (check) | `metadata, use :func:'custom_version_option' to render the output yourself.` |
| [src/click/decorators.py:564](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/decorators.py#L564) | not counted (check) | `"""Add a ''--version'' option whose output is produced by ''callback''.` |
| [src/click/shell_completion.py:404](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L404) | not counted (check) | `output = subprocess.run(` |
| [src/click/shell_completion.py:408](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/shell_completion.py#L408) | not counted (check) | `match = re.search(r"^(\d+)\.(\d+)\.\d+", output.stdout.decode())` |
| [src/click/termui.py:93](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L93) | not counted (check) | `rest of the output.` |
| [src/click/termui.py:353](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L353) | not counted (check) | `Yields a writable file-like object which can be used as an output pager.` |
| [src/click/termui.py:502](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L502) | not counted (check) | `together with ''item_show_func'' to customize the output for each` |
| [src/click/termui.py:553](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L553) | not counted (check) | `codes are included anywhere in the progress bar output` |
| [src/click/termui.py:568](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L568) | not counted (check) | `Labels are echoed if the output is not a TTY. Reverts a change` |
| [src/click/termui.py:569](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L569) | not counted (check) | `in 7.0 that removed all output.` |
| [src/click/testing.py:37](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L37) | not counted (check) | `def __init__(self, input: t.BinaryIO, output: t.BinaryIO) -> None:` |
| [src/click/testing.py:39](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L39) | not counted (check) | `self._output = output` |
| [src/click/testing.py:141](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L141) | not counted (check) | `The result is available in the ''output'' attribute.` |
| [src/click/testing.py:146](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L146) | not counted (check) | `output: io.BytesIO` |
| [src/click/testing.py:151](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L151) | not counted (check) | `self.output = io.BytesIO()` |
| [src/click/testing.py:152](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L152) | not counted (check) | `self.stdout = BytesIOCopy(copy_to=self.output)` |
| [src/click/testing.py:153](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L153) | not counted (check) | `self.stderr = BytesIOCopy(copy_to=self.output)` |
| [src/click/testing.py:235](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L235) | not counted (check) | `:param stdout_bytes: The standard output as bytes.` |
| [src/click/testing.py:283](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L283) | definition | `def output(self) -> str:` |
| [src/click/testing.py:284](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L284) | not counted (check) | `"""The terminal output as unicode string, as the user would see it.` |
| [src/click/testing.py:296](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L296) | not counted (check) | `"""The standard output as unicode string."""` |
| [src/click/testing.py:323](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L323) | not counted (check) | `:param charset: the character set for the input and output data.` |
| [src/click/testing.py:331](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L331) | not counted (check) | `:param capture: Selects the output capture strategy. ''sys'' (default)` |
| [src/click/testing.py:337](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L337) | not counted (check) | `output from stale stream references, C extensions, and subprocesses.` |
| [src/click/testing.py:414](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L414) | not counted (check) | `:param color: whether the output should contain color codes. The` |
| [src/click/testing.py:418](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L418) | not counted (check) | `An additional output stream is returned, which is a mix of` |
| [src/click/testing.py:538](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/testing.py#L538) | not counted (check) | `reads from an empty buffer and writes to captured output,` |
| … | 526 more | |

Check: the direct callers are right (as for callers questions); deeper levels apply the same rule to each caller. Real failures come from the sandbox records.

- [x] Matches the code at the pinned commit
- Auditor notes:

## click-unanswerable-001 (unanswerable, test, scip)

**Question:** What does the function `rehydrate_raw_terminal` do, and which functions call it?

**Notes:** Absent: `rehydrate_raw_terminal` appears in no file at this commit and in no SCIP symbol.

Files at the pinned commit that contain `rehydrate_raw_terminal`: 0.

Check: `git grep -n rehydrate_raw_terminal cf19179e8e2f` prints nothing.

- [x] Matches the code at the pinned commit
- Auditor notes:

## httpx-callers-005 (callers, test, scip)

**Question:** Which functions, methods, or tests call `encode_json` (defined in `httpx/_content.py`)?

**Target:** `py:httpx/_content.py:encode_json:function` at [httpx/_content.py:176](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L176)

**Answer (callers), with the reference lines:**

- `py:httpx/_content.py:encode_request:function` at [httpx/_content.py:186](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L186): [httpx/_content.py:216](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L216)
- `py:httpx/_content.py:encode_response:function` at [httpx/_content.py:221](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L221): [httpx/_content.py:238](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L238)

Every line that mentions `encode_json` (3):

| Line | Classified as | Text |
|---|---|---|
| [httpx/_content.py:176](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L176) | definition | `def encode_json(json: Any) -> tuple[dict[str, str], ByteStream]:` |
| [httpx/_content.py:216](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L216) | counted: caller `py:httpx/_content.py:encode_request:function` | `return encode_json(json)` |
| [httpx/_content.py:238](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L238) | counted: caller `py:httpx/_content.py:encode_response:function` | `return encode_json(json)` |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [x] Matches the code at the pinned commit
- Auditor notes:

## httpx-multihop-001 (multihop, test, scip)

**Question:** Which test functions reach `base_url` (defined in `httpx/_client.py`) directly or through exactly one intermediate function?

**Notes:** template: tests-reach-1. Intermediates are non-test functions, methods, classes.

**Target:** `py:httpx/_client.py:BaseClient.base_url:method` at [httpx/_client.py:288](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L288)

**Answer (tests), with the path to the target:**

- `py:tests/client/test_properties.py:test_client_base_url:test` at [tests/client/test_properties.py:4](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L4): calls it directly at [tests/client/test_properties.py:6](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L6), [tests/client/test_properties.py:7](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L7), [tests/client/test_properties.py:8](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L8)
- `py:tests/client/test_properties.py:test_client_base_url_with_trailing_slash:test` at [tests/client/test_properties.py:18](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L18): calls it directly at [tests/client/test_properties.py:20](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L20), [tests/client/test_properties.py:21](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L21), [tests/client/test_properties.py:22](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L22)
- `py:tests/client/test_properties.py:test_client_base_url_without_trailing_slash:test` at [tests/client/test_properties.py:11](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L11): calls it directly at [tests/client/test_properties.py:13](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L13), [tests/client/test_properties.py:14](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L14), [tests/client/test_properties.py:15](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_properties.py#L15)

Check: each path is real; no other test reaches the target in two steps.

- [x] Matches the code at the pinned commit
- Auditor notes:

## remeda-lookup-002 (lookup, dev, scip)

**Question:** Where is the function `uniqueImplementation` defined?

**Answer:** `ts:packages/remeda/src/unique.ts:uniqueImplementation:function` at [packages/remeda/src/unique.ts:44](https://github.com/remeda/remeda/blob/ec4d3f5dad1105c522c83ec4877e04937cb2405e/packages/remeda/src/unique.ts#L44)

Definitions named `uniqueImplementation` in the repo: 1.

Check: the linked line defines the symbol the question names.

- [x] Matches the code at the pinned commit
- Auditor notes:

## requests-multihop-002 (multihop, dev, scip)

**Question:** Which test functions reach `PreparedRequest.prepare_url` (defined in `src/requests/models.py`) directly or through exactly one intermediate function?

**Notes:** template: tests-reach-1. Intermediates are non-test functions, methods, classes.

**Target:** `py:src/requests/models.py:PreparedRequest.prepare_url:method` at [src/requests/models.py:481](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/models.py#L481)

**Answer (tests), with the path to the target:**

- `py:tests/test_requests.py:test_data_argument_accepts_tuples:test` at [tests/test_requests.py:2674](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2674): calls `py:src/requests/models.py:PreparedRequest.prepare:method` at [tests/test_requests.py:2679](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2679), which calls it at [src/requests/models.py:439](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/models.py#L439)
- `py:tests/test_requests.py:test_json_encodes_as_bytes:test` at [tests/test_requests.py:2615](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2615): calls `py:src/requests/models.py:PreparedRequest.prepare:method` at [tests/test_requests.py:2619](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2619), which calls it at [src/requests/models.py:439](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/models.py#L439)
- `py:tests/test_requests.py:test_prepared_copy:test` at [tests/test_requests.py:2705](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2705): calls `py:src/requests/models.py:PreparedRequest.prepare:method` at [tests/test_requests.py:2708](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2708), which calls it at [src/requests/models.py:439](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/models.py#L439)
- `py:tests/test_requests.py:test_proxy_env_vars_override_default:test` at [tests/test_requests.py:2653](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2653): calls `py:src/requests/models.py:PreparedRequest.prepare:method` at [tests/test_requests.py:2656](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L2656), which calls it at [src/requests/models.py:439](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/models.py#L439)

Check: each path is real; no other test reaches the target in two steps.

- [x] Matches the code at the pinned commit
- Auditor notes:

## requests-unanswerable-002 (unanswerable, test, human)

**Question:** What is the median response time of the requests project's production deployment, according to the maintainers' monitoring dashboards?

**Notes:** Outside the code: no file in the repo can answer this.

Check: nothing in the repository could answer this; a refusal is right.

- [x] Matches the code at the pinned commit
- Auditor notes:

## zod-callers-002 (callers, dev, scip)

**Question:** Which functions, methods, or tests call `stripTabAndNewline` (defined in `packages/zod/src/v4/core/schemas.ts`)?

**Target:** `ts:packages/zod/src/v4/core/schemas.ts:stripTabAndNewline:function` at [packages/zod/src/v4/core/schemas.ts:538](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L538)

**Answer (callers), with the reference lines:**

- `ts:packages/zod/src/v4/core/compile.ts:generateStringFormatCheck:function` at [packages/zod/src/v4/core/compile.ts:712](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L712): [packages/zod/src/v4/core/compile.ts:771](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L771)
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodURL:function` at [packages/zod/src/v4/core/schemas.ts:552](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L552): [packages/zod/src/v4/core/schemas.ts:608](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L608)

Every line that mentions `stripTabAndNewline` (4):

| Line | Classified as | Text |
|---|---|---|
| [packages/zod/src/v4/core/compile.ts:16](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L16) | import (not a call) | `stripTabAndNewline,` |
| [packages/zod/src/v4/core/compile.ts:771](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L771) | counted: caller `ts:packages/zod/src/v4/core/compile.ts:generateStringFormatCheck:function` | `const outputExpr = formatDef.normalize ? '${urlVar}.href' : '${addConstant(ctx, stripTabAn` |
| [packages/zod/src/v4/core/schemas.ts:538](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L538) | definition | `export function stripTabAndNewline(value: string): string {` |
| [packages/zod/src/v4/core/schemas.ts:608](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L608) | counted: caller `ts:packages/zod/src/v4/core/schemas.ts:$ZodURL:function` | `payload.value = def.normalize ? url.href : stripTabAndNewline(trimmed);` |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [x] Matches the code at the pinned commit
- Auditor notes:

## zod-callers-004 (callers, test, scip)

**Question:** Which functions, methods, or tests call `returns` (defined in `packages/zod/src/v3/types.ts`)?

**Target:** `ts:packages/zod/src/v3/types.ts:ZodFunction.returns:method` at [packages/zod/src/v3/types.ts:3921](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3921)

**Answer (callers), with the reference lines:**

- `ts:packages/zod/src/v3/tests/function.test.ts:allow extra parameters:test` at [packages/zod/src/v3/tests/function.test.ts:214](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L214): [packages/zod/src/v3/tests/function.test.ts:218](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L218)
- `ts:packages/zod/src/v3/tests/function.test.ts:args method:test` at [packages/zod/src/v3/tests/function.test.ts:62](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L62): [packages/zod/src/v3/tests/function.test.ts:71](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L71)
- `ts:packages/zod/src/v3/tests/function.test.ts:async method parsing:test` at [packages/zod/src/v3/tests/function.test.ts:47](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L47): [packages/zod/src/v3/tests/function.test.ts:50](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L50)
- `ts:packages/zod/src/v3/tests/function.test.ts:fallback to OuterTypeOfFunction:test` at [packages/zod/src/v3/tests/function.test.ts:250](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L250): [packages/zod/src/v3/tests/function.test.ts:254](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L254)
- `ts:packages/zod/src/v3/tests/function.test.ts:function with async refinements:test` at [packages/zod/src/v3/tests/function.test.ts:169](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L169): [packages/zod/src/v3/tests/function.test.ts:173](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L173)
- `ts:packages/zod/src/v3/tests/function.test.ts:inference with transforms:test` at [packages/zod/src/v3/tests/function.test.ts:237](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L237): [packages/zod/src/v3/tests/function.test.ts:241](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L241)
- `ts:packages/zod/src/v3/tests/function.test.ts:method parsing:test` at [packages/zod/src/v3/tests/function.test.ts:32](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L32): [packages/zod/src/v3/tests/function.test.ts:35](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L35)
- `ts:packages/zod/src/v3/tests/function.test.ts:non async function with async refinements should fail:test` at [packages/zod/src/v3/tests/function.test.ts:194](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L194): [packages/zod/src/v3/tests/function.test.ts:198](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L198)
- `ts:packages/zod/src/v3/tests/function.test.ts:params and returnType getters:test` at [packages/zod/src/v3/tests/function.test.ts:227](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L227): [packages/zod/src/v3/tests/function.test.ts:228](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L228)

Every line that mentions `returns` (85):

| Line | Classified as | Text |
|---|---|---|
| [packages/bench/bench-invalid.ts:26](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/bench-invalid.ts#L26) | not counted (check) | `// Core _run function - returns T \| $ZodError` |
| [packages/bench/compile-matrix.ts:7](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-matrix.ts#L7) | not counted (check) | `// Methodology: absolute ops/sec on a laptop drifts by tens of percent between runs (therm` |
| [packages/bench/compile-vs-arktype.ts:328](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L328) | not counted (check) | `// Check if arktype returns the same object or a new one` |
| [packages/bench/compile-vs-arktype.ts:336](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L336) | not counted (check) | `console.log("Arktype returns same object:", arktypeResult === simpleData);` |
| [packages/bench/compile-vs-arktype.ts:337](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L337) | not counted (check) | `console.log("Zod returns same object:", zodResult.success && zodResult.data === simpleData` |
| [packages/bench/compile-vs-arktype.ts:338](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L338) | not counted (check) | `console.log("Handwritten returns same object:", handwrittenResult === simpleData);` |
| [packages/bench/compile-vs-arktype.ts:346](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L346) | not counted (check) | `console.log("Arktype nested returns same object:", arktypeNestedResult === nestedData);` |
| [packages/bench/compile-vs-arktype.ts:347](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/compile-vs-arktype.ts#L347) | not counted (check) | `console.log("Zod nested returns same object:", zodNestedResult.success && zodNestedResult.` |
| [packages/bench/memory/leaks.ts:5](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/memory/leaks.ts#L5) | not counted (check) | `* then DROPS every reference it created. A correct scenario returns to its` |
| [packages/bench/moltar-libs.ts:121](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/moltar-libs.ts#L121) | not counted (check) | `// parseSafe needs unknown keys deleted at every level, which is a different arktype confi` |
| [packages/bench/moltar-libs.ts:307](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/moltar-libs.ts#L307) | not counted (check) | `? "parseSafe — returns a new object with unknown keys removed:"` |
| [packages/bench/moltar-libs.ts:308](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/bench/moltar-libs.ts#L308) | not counted (check) | `: "assertLoose — returns a boolean, ignores unknown keys, allocates nothing:"` |
| [packages/zod/src/v3/tests/error.test.ts:501](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/error.test.ts#L501) | not counted (check) | `test("enum with message returns the custom error message", () => {` |
| [packages/zod/src/v3/tests/function.test.ts:35](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L35) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:method parsing:test` | `method: z.function().args(z.string()).returns(z.number()),` |
| [packages/zod/src/v3/tests/function.test.ts:50](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L50) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:async method parsing:test` | `method: z.function().args(z.string()).returns(z.promise(z.number())),` |
| [packages/zod/src/v3/tests/function.test.ts:71](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L71) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:args method:test` | `const t3 = t2.returns(z.boolean());` |
| [packages/zod/src/v3/tests/function.test.ts:173](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L173) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:function with async refinements:test` | `.returns(z.promise(z.number().refine(async (val) => val > 10)))` |
| [packages/zod/src/v3/tests/function.test.ts:198](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L198) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:non async function with async refinements should fail:test` | `.returns(z.number().refine(async (val) => val > 10))` |
| [packages/zod/src/v3/tests/function.test.ts:218](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L218) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:allow extra parameters:test` | `.returns(z.boolean())` |
| [packages/zod/src/v3/tests/function.test.ts:228](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L228) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:params and returnType getters:test` | `const func = z.function().args(z.string()).returns(z.string());` |
| [packages/zod/src/v3/tests/function.test.ts:241](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L241) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:inference with transforms:test` | `.returns(z.object({ val: z.number() }));` |
| [packages/zod/src/v3/tests/function.test.ts:254](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/tests/function.test.ts#L254) | counted: caller `ts:packages/zod/src/v3/tests/function.test.ts:fallback to OuterTypeOfFunction:test` | `.returns(z.object({ arg: z.number() }).transform((val) => val.arg));` |
| [packages/zod/src/v3/types.ts:3805](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3805) | not counted (check) | `returns: Returns;` |
| [packages/zod/src/v3/types.ts:3849](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3849) | not counted (check) | `function makeReturnsIssue(returns: any, error: ZodError): ZodIssue {` |
| [packages/zod/src/v3/types.ts:3851](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3851) | not counted (check) | `data: returns,` |
| [packages/zod/src/v3/types.ts:3866](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3866) | not counted (check) | `if (this._def.returns instanceof ZodPromise) {` |
| [packages/zod/src/v3/types.ts:3877](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3877) | not counted (check) | `const parsedReturns = await (me._def.returns as unknown as ZodPromise<ZodTypeAny>)._def.ty` |
| [packages/zod/src/v3/types.ts:3895](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3895) | not counted (check) | `const parsedReturns = me._def.returns.safeParse(result, params);` |
| [packages/zod/src/v3/types.ts:3909](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3909) | not counted (check) | `return this._def.returns;` |
| [packages/zod/src/v3/types.ts:3921](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3921) | definition | `returns<NewReturnType extends ZodType<any, any, any>>(returnType: NewReturnType): ZodFunct` |
| [packages/zod/src/v3/types.ts:3924](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3924) | not counted (check) | `returns: returnType,` |
| [packages/zod/src/v3/types.ts:3946](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3946) | not counted (check) | `static create<T extends AnyZodTuple, U extends ZodTypeAny>(args: T, returns: U): ZodFuncti` |
| [packages/zod/src/v3/types.ts:3949](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3949) | not counted (check) | `returns: U,` |
| [packages/zod/src/v3/types.ts:3952](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3952) | not counted (check) | `static create(args?: AnyZodTuple, returns?: ZodTypeAny, params?: RawCreateParams) {` |
| [packages/zod/src/v3/types.ts:3955](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v3/types.ts#L3955) | not counted (check) | `returns: returns \|\| ZodUnknown.create(),` |
| [packages/zod/src/v4/classic/schemas.ts:266](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L266) | not counted (check) | `// overloaded: meta() returns the registered metadata, meta(data) returns a clone with 'da` |
| [packages/zod/src/v4/classic/schemas.ts:1111](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L1111) | not counted (check) | `/** @deprecated Number schemas no longer accept infinite values, so this always returns 't` |
| [packages/zod/src/v4/classic/tests/array.test.ts:224](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/array.test.ts#L224) | not counted (check) | `// test("returns custom error message without duplicate elements", () => {` |
| [packages/zod/src/v4/classic/tests/array.test.ts:236](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/array.test.ts#L236) | not counted (check) | `// test("returns error message with duplicate elements", () => {` |
| [packages/zod/src/v4/classic/tests/array.test.ts:248](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/array.test.ts#L248) | not counted (check) | `// test("returns custom error message with duplicate elements", () => {` |
| … | 45 more | |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [x] Matches the code at the pinned commit
- Auditor notes:

## zod-lookup-002 (lookup, test, scip)

**Question:** Where is the function `generateNonOptionalCheck` defined?

**Answer:** `ts:packages/zod/src/v4/core/compile.ts:generateNonOptionalCheck:function` at [packages/zod/src/v4/core/compile.ts:1596](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L1596)

Definitions named `generateNonOptionalCheck` in the repo: 1.

Check: the linked line defines the symbol the question names.

- [x] Matches the code at the pinned commit
- Auditor notes:
