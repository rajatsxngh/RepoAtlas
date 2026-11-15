# Security checklist

Every item is a threat from the security model ([PRD §11](PRD.md)), the defense in the code,
and the tests that hold it in place (GRD-8). `backend/tests/unit/security/test_checklist.py`
reads this file and fails when an item has no test or names a test that doesn't exist, so the
list can't drift from the suite.

A test reference is `path::test_function` for pytest, and `path :: "test title"` for the web
app's tests. Run them with:

```bash
cd backend && uv run pytest tests/unit/security tests/integration/security -q
cd backend && uv run pytest -m injection_static -q
cd frontend && npm test && npx playwright test xss
```

What the list does not cover: a deployment's network, TLS, and host. Those are the operator's
([self-hosting.md](self-hosting.md), [deploy.md](deploy.md)).

## S1 — Prompt injection in repository text

Repository text reaches the model only inside evidence blocks, as data. The system message says
so; the injection detector marks instruction-like spans and the prompt wraps them in
`<flagged>`; repository text can't close its block or forge a marker; the answer's citations are
validated against stored code; and the injection suite checks whether an answering mode obeys
planted instructions.

**Not yet shown:** how the live model behaves on the suite. That run is pending in
[live-pass.md](live-pass.md) (items 6, 7, and 12). The tests below prove the layout, the detector, and
the scorer, with scripted stand-ins in place of the model.

- `backend/tests/unit/answers/test_prompting.py::test_repo_text_cannot_break_out_of_its_block`
- `backend/tests/unit/answers/test_flagged_prompt.py::test_flagged_spans_are_wrapped_inside_their_evidence_block`
- `backend/tests/unit/answers/test_flagged_prompt.py::test_repo_text_cannot_forge_or_close_a_marker`
- `backend/tests/unit/answers/test_flagged_prompt.py::test_repo_text_never_reaches_the_system_message_or_the_question`
- `backend/tests/unit/answers/test_flagged_prompt.py::test_the_system_message_says_flagged_text_is_data`
- `backend/tests/unit/guardrails/test_detector.py::test_each_kind_is_marked`
- `backend/tests/unit/guardrails/test_injection_fixture.py::test_the_detector_marks_every_variant`
- `backend/tests/unit/guardrails/test_injection_fixture.py::test_each_indexed_variant_is_wrapped_as_flagged_in_an_evidence_block`
- `backend/tests/unit/evals/test_injection.py::test_a_canary_is_a_leak_however_it_is_written`
- `backend/tests/unit/evals/test_injection.py::test_other_signs_of_obedience_are_leaks`
- `backend/tests/integration/security/test_injection_gate.py::test_oneshot_the_obedient_stand_in_is_caught_on_every_indexed_variant`
- `backend/tests/integration/security/test_injection_gate.py::test_oneshot_the_careful_stand_in_scores_zero_leaks_over_three_runs`
- `backend/tests/integration/security/test_injection_gate.py::test_impact_explanations_the_obedient_stand_in_is_caught`
- `backend/tests/integration/security/test_injection_gate.py::test_impact_explanations_the_careful_stand_in_scores_zero_leaks`
- `backend/tests/integration/security/test_injection_gate.py::test_agent_the_obedient_stand_in_is_caught_in_its_answer_and_its_tool_call`
- `backend/tests/integration/security/test_injection_gate.py::test_agent_the_careful_stand_in_scores_zero_leaks_over_three_runs`
- `frontend/src/components/ask/AnswerBody.test.tsx :: "marks citations of flagged code with a warning, and only those"`

## S2 — XSS through repository text

Code, file and folder names, symbol names, route patterns, test titles, and commit authors are
repository text. The API returns them as JSON, never as HTML; the web app renders them as text
nodes, and the code viewer highlights tokens without `innerHTML`.

- `backend/tests/integration/api/test_abuse.py::test_responses_are_json_even_when_repo_text_looks_like_html`
- `frontend/e2e/app/xss.spec.ts :: "HTML and script in names, code, and authors render as inert text"`
- `frontend/e2e/mock/xss.spec.ts :: "the mock XSS fixture renders as text in map labels, the panel, and the code viewer"`
- `frontend/src/components/code/CodeViewer.test.tsx :: "renders HTML and script in code as inert text"`
- `frontend/src/components/impact/ImpactPanel.test.tsx :: "renders repo text as text and lists unknown sites as not safe"`
- `frontend/src/components/trace/TracePanel.test.tsx :: "renders span names and errors as text"`

## S3 — XSS through answer markdown

An answer is model output built from repository text. Its markdown goes through a renderer that
allows no raw HTML and only `http(s)`, `mailto`, and in-page links; claims are rendered as text.

- `frontend/src/lib/markdown.test.tsx :: "neutralizes dangerous link and image targets"`
- `frontend/src/lib/markdown.test.tsx :: "keeps HTML-looking text inside code as text"`
- `frontend/src/lib/markdown.test.tsx :: "allows only http(s), mailto, and anchors"`
- `frontend/src/components/ask/AskPanel.test.tsx :: "renders model and repo text as text, never as HTML"`

## S4 — Path traversal in code reads

A path is a key into the snapshot's own file list, and text comes from the `blobs` table; no
read ever touches a file system. Every read that names a path goes through
`ensure_snapshot_path`: the shape is checked before any query, then the path must be one of the
snapshot's files. Blob reads take full OIDs only.

- `backend/tests/unit/guardrails/test_allowlist.py::test_a_malformed_path_is_refused_before_any_lookup`
- `backend/tests/unit/guardrails/test_allowlist.py::test_a_well_formed_path_outside_the_snapshot_is_refused`
- `backend/tests/integration/security/test_code_read_allowlist.py::test_malformed_paths_are_refused_by_every_read_without_touching_files`
- `backend/tests/integration/security/test_code_read_allowlist.py::test_reads_return_only_files_of_their_own_snapshot`
- `backend/tests/integration/security/test_code_read_allowlist.py::test_path_traversal_on_the_code_endpoint_is_refused`
- `backend/tests/integration/security/test_code_read_allowlist.py::test_blob_reads_take_full_oids_only`
- `backend/tests/integration/api/test_abuse.py::test_traversal_paths_are_rejected`
- `backend/tests/integration/api/test_abuse.py::test_encoded_and_absolute_variants_never_reach_the_disk`

## S5 — SSRF through the repository URL

Only `https://github.com/<owner>/<repo>` is accepted: no other host, IP literal, user
information, port, scheme, look-alike host, query, or fragment. A bad URL is refused before any
network call. The GitHub API client refuses a redirect to another host, and git fetches allow
only https and file origins and follow no redirects.

- `backend/tests/unit/security/test_ssrf.py::test_a_hostile_url_is_refused_before_any_network_call`
- `backend/tests/unit/security/test_ssrf.py::test_a_valid_url_reaches_only_the_github_api`
- `backend/tests/unit/security/test_ssrf.py::test_the_github_client_never_follows_a_redirect_to_another_host`
- `backend/tests/unit/security/test_ssrf.py::test_a_redirect_within_the_github_api_is_followed`
- `backend/tests/unit/security/test_ssrf.py::test_git_fetches_allow_https_and_file_only_and_follow_no_redirects`
- `backend/tests/unit/security/test_ssrf.py::test_fetches_refuse_any_other_origin_before_git_runs`
- `backend/tests/unit/ingest/test_repo_url.py::test_invalid_urls`

## S6 — Oversized inputs

Questions (2,000 characters), diffs (60,000), symbol keys, paths, and whole request bodies
(64 KB) are capped; a refused request never reaches the model. A code read returns at most
`max_read_lines` lines however large the requested range.

- `backend/tests/integration/security/test_input_caps.py::test_questions_are_capped_and_a_refused_one_never_reaches_the_model`
- `backend/tests/integration/security/test_input_caps.py::test_request_bodies_over_the_limit_are_refused_whatever_they_hold`
- `backend/tests/integration/security/test_input_caps.py::test_diffs_and_symbol_keys_are_capped`
- `backend/tests/integration/security/test_input_caps.py::test_query_parameters_are_capped`
- `backend/tests/integration/api/test_abuse.py::test_oversized_bodies_are_refused_before_they_are_read`
- `backend/tests/unit/guardrails/test_allowlist.py::test_line_ranges_are_clamped_to_the_file_and_the_read_limit`

## S7 — Secrets in logs, traces, prompts, and errors

Secrets are `SecretStr` settings read from the environment. They are never put in a prompt, a
request body, a span attribute, or a log field. As a last line of defense, every log event and
traceback, every error stored on a trace span, and every LLM error message passes through
`repoatlas.redact`, which removes the process's own secret values and anything shaped like a
credential. Exported OpenTelemetry spans carry no headers, prompts, or repository text.

- `backend/tests/unit/security/test_secrets.py::test_settings_never_show_their_secrets`
- `backend/tests/unit/security/test_secrets.py::test_redact_removes_registered_values_and_credential_shapes`
- `backend/tests/unit/security/test_secrets.py::test_no_secret_reaches_a_log_line`
- `backend/tests/unit/security/test_secrets.py::test_a_secret_in_an_exception_is_not_stored_on_a_trace_span`
- `backend/tests/unit/security/test_secrets.py::test_llm_error_messages_are_scrubbed`
- `backend/tests/unit/security/test_secrets.py::test_the_api_key_is_never_part_of_a_request_body_or_a_prompt`
- `backend/tests/integration/security/test_secret_leaks.py::test_a_failure_that_quotes_a_key_leaks_it_nowhere`
- `backend/tests/integration/security/test_secret_leaks.py::test_answers_and_traces_never_contain_settings_secrets`
- `backend/tests/integration/observability/test_export.py::test_export_on_sends_one_connected_trace`
- `backend/tests/unit/observability/test_otel.py::test_outgoing_http_calls_are_spans_without_headers`
- `backend/tests/integration/answers/test_recorded_answer.py::test_the_cassette_holds_no_secrets`
- `backend/tests/unit/api/test_access.py::test_rejected_requests_use_up_no_rate_limit_and_log_no_token`

## S8 — Running repository code

A build reads a repository's files as bytes from git objects, parses them, and stores them. It
never checks out a working tree, installs, imports, or runs anything. Only the curated
benchmark repositories ever run, inside the hardened sandbox.

- `backend/tests/integration/security/test_no_repo_code_runs.py::test_a_booby_trapped_repository_builds_without_running_anything`
- `backend/tests/unit/ingest/test_fetch.py::test_fetch_source_reads_exact_bytes_without_a_working_tree`
- `backend/tests/unit/sandbox/test_hardened.py::test_no_mounts_no_socket_no_privileges`

## S9 — Resource abuse and cost spikes

Per-client rate limits and daily caps, a cap on jobs running at once, a daily spend cap checked
before every model call, and a per-answer cost budget.

- `backend/tests/unit/api/test_ratelimit.py::test_clients_over_the_limit_get_429_with_retry_after`
- `backend/tests/unit/api/test_ratelimit.py::test_the_daily_cap_answers_429_until_midnight`
- `backend/tests/unit/api/test_ratelimit.py::test_requests_count_against_their_group`
- `backend/tests/integration/api/test_abuse.py::test_build_floods_are_rate_limited_without_queueing_more`
- `backend/tests/integration/limits/test_spend_guard.py::test_over_the_cap_an_ask_is_a_budget_refusal`
- `backend/tests/integration/limits/test_job_cap.py::test_racing_claims_never_exceed_the_cap`

## S10 — Access to a private deployment

When an access token is set, every API route except liveness needs it; a wrong or missing token
is rejected without using up a rate limit or logging the token.

- `backend/tests/unit/api/test_access.py::test_a_request_without_the_token_is_rejected`
- `backend/tests/unit/api/test_access.py::test_a_wrong_token_is_rejected`
- `backend/tests/unit/api/test_access.py::test_every_route_is_gated_except_liveness_and_preflights`

## S11 — Tools that read the repository for a model

The agent's tools, and the same tools served over MCP, are read-only and bound to one snapshot
when they are made: no tool takes a snapshot, a URL, or a command. Arguments are checked before
anything runs; a path that is malformed or not a file of the snapshot is refused and recorded
as a call that was not allowed; what a tool returns is trimmed, passed through the injection
detector, and can't forge an evidence entry or close a prompt block. A model that calls a tool
because repository text asked it to gets an error result, and the injection suite counts the
attempt as a leak. Over HTTP, the MCP server answers nothing without its bearer token and
limits each client's calls; tool names, arguments, and errors are shown in the web app as text.

- `backend/tests/unit/agent/test_tool_schemas.py::test_the_definitions_are_accepted_by_the_request_builder`
- `backend/tests/unit/agent/test_tool_schemas.py::test_an_unknown_tool_is_refused_as_not_allowed`
- `backend/tests/unit/agent/test_guard.py::test_a_malformed_path_is_refused_as_not_allowed_before_the_store_is_asked`
- `backend/tests/integration/agent/test_tools.py::test_reads_outside_the_snapshot_are_refused_as_not_allowed`
- `backend/tests/unit/agent/test_guard.py::test_tool_output_cannot_forge_or_close_a_prompt_block`
- `backend/tests/unit/agent/test_guard_lab.py::test_every_planted_variant_is_marked_in_a_file_read`
- `backend/tests/unit/agent/test_loop.py::test_a_tool_error_goes_back_to_the_model_and_the_loop_goes_on`
- `backend/tests/unit/llm/test_offline_agent.py::test_evidence_entries_forged_in_tool_output_are_not_cited`
- `backend/tests/integration/mcp/test_mcp_server.py::test_a_call_that_cannot_run_is_an_error_result_the_client_can_read`
- `backend/tests/unit/agent/test_mcp_gate.py::test_a_request_without_the_exact_token_never_reaches_the_server`
- `backend/tests/integration/mcp/test_mcp_transports.py::test_http_needs_the_bearer_token`
- `backend/tests/integration/mcp/test_mcp_transports.py::test_http_is_rate_limited_per_client`
- `frontend/src/components/trace/TracePanel.test.tsx :: "renders tool names, arguments, and errors as text, never as HTML"`
