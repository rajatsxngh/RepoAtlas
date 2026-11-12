# Benchmark 1.0.0 audit

A seeded sample (`--sample 0.1 --seed 1`) of 11 of the 110 generated questions (flow questions are reviewed separately). Check each one against the code on GitHub at the pinned commit; links point there. When one is wrong, fix the generator, regenerate, and audit again.

| Question | Type | Verdict |
|---|---|---|
| click-impact-002 | impact | pending |
| click-impact-004 | impact | pending |
| click-unanswerable-001 | unanswerable | pending |
| httpx-callers-005 | callers | pending |
| httpx-multihop-001 | multihop | pending |
| remeda-lookup-002 | lookup | pending |
| requests-multihop-002 | multihop | pending |
| requests-unanswerable-002 | unanswerable | pending |
| zod-callers-002 | callers | pending |
| zod-callers-004 | callers | pending |
| zod-lookup-002 | lookup | pending |

## click-impact-002 (impact, dev, sandbox)

**Question:** What breaks if I change `eta` (defined in `src/click/_termui_impl.py`)? List the functions, methods, and tests that depend on it.

**Notes:** static: callers up to depth 3 (7). real: 9 confirmed failing tests from the sandbox mutation (raise, seed 7); 0 unmapped.

**Target:** `py:src/click/_termui_impl.py:ProgressBar.eta:method` at [src/click/_termui_impl.py:169](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L169)

**Static set:** 7 definitions within 3 caller hops. The 2 direct callers, with the reference lines:

- `py:src/click/_termui_impl.py:ProgressBar.format_eta:method` at [src/click/_termui_impl.py:174](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L174): [src/click/_termui_impl.py:176](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L176)
- `py:tests/test_termui.py:test_progressbar_eta:test` at [tests/test_termui.py:150](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L150): [tests/test_termui.py:152](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L152)

**Real failing tests** (sandbox mutation): 9, first 10:

- `tests/test_termui.py::test_progressbar_eta[False-5]` → `py:tests/test_termui.py:test_progressbar_eta:test`
- `tests/test_termui.py::test_progressbar_eta[True-0]` → `py:tests/test_termui.py:test_progressbar_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[0-00:00:00]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[30-00:00:30]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[90-00:01:30]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[900-00:15:00]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[9000-02:30:00]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_format_eta[99999999999-1157407d 09:46:39]` → `py:tests/test_termui.py:test_progressbar_format_eta:test`
- `tests/test_termui.py::test_progressbar_update` → `py:tests/test_termui.py:test_progressbar_update:test`

Every line that mentions `eta` (7):

| Line | Classified as | Text |
|---|---|---|
| [src/click/_termui_impl.py:169](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L169) | definition | `def eta(self) -> float:` |
| [src/click/_termui_impl.py:176](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/_termui_impl.py#L176) | counted: caller `py:src/click/_termui_impl.py:ProgressBar.format_eta:method` | `t = int(self.eta)` |
| [src/click/termui.py:546](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/termui.py#L546) | not counted (check) | `:param info_sep: the separator between multiple info items (eta etc.)` |
| [tests/test_termui.py:152](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L152) | counted: caller `py:tests/test_termui.py:test_progressbar_eta:test` | `assert progress.eta == expected` |
| [tests/test_termui.py:156](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L156) | not counted (check) | `"eta, expected",` |
| [tests/test_termui.py:167](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L167) | not counted (check) | `def test_progressbar_format_eta(runner, eta, expected):` |
| [tests/test_termui.py:168](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/tests/test_termui.py#L168) | not counted (check) | `with _create_progress(1, eta_known=eta is not None, avg=[eta]) as progress:` |

Check: the direct callers are right (as for callers questions); deeper levels apply the same rule to each caller. Real failures come from the sandbox records.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## click-impact-004 (impact, test, sandbox)

**Question:** What breaks if I change `check_iter` (defined in `src/click/core.py`)? List the functions, methods, and tests that depend on it.

**Notes:** static: callers up to depth 3 (172). real: 110 confirmed failing tests from the sandbox mutation (raise, seed 7); 0 unmapped.

**Target:** `py:src/click/core.py:Parameter.type_cast_value.check_iter:function` at [src/click/core.py:2576](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2576)

**Static set:** 172 definitions within 3 caller hops. The 1 direct callers, with the reference lines:

- `py:src/click/core.py:Parameter.type_cast_value:method` at [src/click/core.py:2566](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2566): [src/click/core.py:2597](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2597), [src/click/core.py:2602](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2602), [src/click/core.py:2618](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2618)

**Real failing tests** (sandbox mutation): 110, first 10:

- `tests/test_arguments.py::test_bad_defaults_for_nargs[-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[12-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[7.9-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[False-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[True-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[Yo-Error: Invalid value for '[A]...': Value must be an iterable.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[default10-Error: Invalid value for '[A]...': Takes 2 values but 3 were given.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[default11-Error: Invalid value for '[A]...': Takes 2 values but 0 were given.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[default12-Error: Invalid value for '[A]...': Takes 2 values but 1 was given.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`
- `tests/test_arguments.py::test_bad_defaults_for_nargs[default13-Error: Invalid value for '[A]...': Takes 2 values but 3 were given.]` → `py:tests/test_arguments.py:test_bad_defaults_for_nargs:test`

Every line that mentions `check_iter` (4):

| Line | Classified as | Text |
|---|---|---|
| [src/click/core.py:2576](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2576) | definition | `def check_iter(value: t.Any) -> cabc.Iterator[t.Any]:` |
| [src/click/core.py:2597](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2597) | counted: caller `py:src/click/core.py:Parameter.type_cast_value:method` | `return tuple(self.type(x, self, ctx) for x in check_iter(value))` |
| [src/click/core.py:2602](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2602) | counted: caller `py:src/click/core.py:Parameter.type_cast_value:method` | `value = tuple(check_iter(value))` |
| [src/click/core.py:2618](https://github.com/pallets/click/blob/cf19179e8e2fd54c601378cec9e0d21bb12b6fb1/src/click/core.py#L2618) | counted: caller `py:src/click/core.py:Parameter.type_cast_value:method` | `return tuple(convert(x) for x in check_iter(value))` |

Check: the direct callers are right (as for callers questions); deeper levels apply the same rule to each caller. Real failures come from the sandbox records.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## click-unanswerable-001 (unanswerable, test, scip)

**Question:** What does the function `rehydrate_raw_terminal` do, and which functions call it?

**Notes:** Absent: `rehydrate_raw_terminal` appears in no file at this commit and in no SCIP symbol.

Files at the pinned commit that contain `rehydrate_raw_terminal`: 0.

Check: `git grep -n rehydrate_raw_terminal cf19179e8e2f` prints nothing.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## httpx-callers-005 (callers, test, scip)

**Question:** Which functions, methods, or tests call `Headers.items` (defined in `httpx/_models.py`)?

**Target:** `py:httpx/_models.py:Headers.items:method` at [httpx/_models.py:216](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L216)

**Answer (callers), with the reference lines:**

- `py:tests/client/test_headers.py:echo_repeated_headers_items:function` at [tests/client/test_headers.py:18](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_headers.py#L18): [tests/client/test_headers.py:19](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/client/test_headers.py#L19)
- `py:tests/models/test_headers.py:test_headers:test` at [tests/models/test_headers.py:6](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_headers.py#L6): [tests/models/test_headers.py:20](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_headers.py#L20)

Every line that mentions `items` (60):

| Line | Classified as | Text |
|---|---|---|
| [httpx/_api.py:77](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_api.py#L77) | not counted (check) | `* **cookies** - *(optional)* Dictionary of Cookie items to include in the` |
| [httpx/_auth.py:317](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_auth.py#L317) | not counted (check) | `for i, (field, value) in enumerate(header_fields.items()):` |
| [httpx/_client.py:246](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L246) | not counted (check) | `for key, url in get_environment_proxies().items()` |
| [httpx/_client.py:615](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L615) | not counted (check) | `* **cookies** - *(optional)* Dictionary of Cookie items to include when` |
| [httpx/_client.py:709](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L709) | not counted (check) | `for key, proxy in proxy_map.items()` |
| [httpx/_client.py:713](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L713) | not counted (check) | `{URLPattern(key): transport for key, transport in mounts.items()}` |
| [httpx/_client.py:716](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L716) | not counted (check) | `self._mounts = dict(sorted(self._mounts.items()))` |
| [httpx/_client.py:765](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L765) | not counted (check) | `for pattern, transport in self._mounts.items():` |
| [httpx/_client.py:1329](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1329) | not counted (check) | `* **cookies** - *(optional)* Dictionary of Cookie items to include when` |
| [httpx/_client.py:1424](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1424) | not counted (check) | `for key, proxy in proxy_map.items()` |
| [httpx/_client.py:1428](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1428) | not counted (check) | `{URLPattern(key): transport for key, transport in mounts.items()}` |
| [httpx/_client.py:1430](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1430) | not counted (check) | `self._mounts = dict(sorted(self._mounts.items()))` |
| [httpx/_client.py:1479](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_client.py#L1479) | not counted (check) | `for pattern, transport in self._mounts.items():` |
| [httpx/_content.py:140](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_content.py#L140) | not counted (check) | `for key, value in data.items():` |
| [httpx/_main.py:196](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_main.py#L196) | not counted (check) | `for key, value in cert.items():` |
| [httpx/_models.py:131](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L131) | not counted (check) | `items: typing.Iterable[tuple[typing.AnyStr, typing.AnyStr]],` |
| [httpx/_models.py:133](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L133) | not counted (check) | `for k, v in items:` |
| [httpx/_models.py:154](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L154) | not counted (check) | `for k, v in headers.items():` |
| [httpx/_models.py:198](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L198) | not counted (check) | `Returns a list of the raw header items, as byte pairs.` |
| [httpx/_models.py:216](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L216) | definition | `def items(self) -> typing.ItemsView[str, str]:` |
| [httpx/_models.py:218](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L218) | not counted (check) | `Return '(key, value)' items of headers. Concatenate headers` |
| [httpx/_models.py:229](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L229) | not counted (check) | `return values_dict.items()` |
| [httpx/_models.py:293](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L293) | not counted (check) | `items = [` |
| [httpx/_models.py:299](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L299) | not counted (check) | `if items:` |
| [httpx/_models.py:300](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L300) | not counted (check) | `return ", ".join(items)` |
| [httpx/_models.py:442](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L442) | not counted (check) | `for key, value in default_headers.items():` |
| [httpx/_models.py:504](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L504) | not counted (check) | `for name, value in self.__dict__.items()` |
| [httpx/_models.py:509](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L509) | not counted (check) | `for name, value in state.items():` |
| [httpx/_models.py:572](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L572) | not counted (check) | `for key, value in default_headers.items():` |
| [httpx/_models.py:865](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L865) | not counted (check) | `for name, value in self.__dict__.items()` |
| [httpx/_models.py:870](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L870) | not counted (check) | `for name, value in state.items():` |
| [httpx/_models.py:1088](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L1088) | not counted (check) | `for key, value in cookies.items():` |
| [httpx/_multipart.py:195](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_multipart.py#L195) | not counted (check) | `for header_name, header_value in self.headers.items():` |
| [httpx/_multipart.py:247](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_multipart.py#L247) | not counted (check) | `for name, value in data.items():` |
| [httpx/_multipart.py:254](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_multipart.py#L254) | not counted (check) | `file_items = files.items() if isinstance(files, typing.Mapping) else files` |
| [httpx/_transports/default.py:105](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_transports/default.py#L105) | not counted (check) | `for from_exc, to_exc in HTTPCORE_EXC_MAP.items():` |
| [httpx/_urlparse.py:266](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_urlparse.py#L266) | not counted (check) | `for key, value in kwargs.items():` |
| [httpx/_urls.py:95](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_urls.py#L95) | not counted (check) | `for key, value in kwargs.items():` |
| [httpx/_urls.py:435](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_urls.py#L435) | not counted (check) | `self._dict = {k: list(v) for k, v in value._dict.items()}` |
| [httpx/_urls.py:452](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_urls.py#L452) | not counted (check) | `for k, v in value.items()` |
| … | 20 more | |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [ ] Matches the code at the pinned commit
- Auditor notes:

## httpx-multihop-001 (multihop, test, scip)

**Question:** Which test functions reach `Cookies.set` (defined in `httpx/_models.py`) directly or through exactly one intermediate function?

**Notes:** template: tests-reach-1. Intermediates are non-test functions, methods, classes.

**Target:** `py:httpx/_models.py:Cookies.set:method` at [httpx/_models.py:1117](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/httpx/_models.py#L1117)

**Answer (tests), with the path to the target:**

- `py:tests/models/test_cookies.py:test_cookies_repr:test` at [tests/models/test_cookies.py:90](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L90): calls it directly at [tests/models/test_cookies.py:92](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L92), [tests/models/test_cookies.py:93](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L93)
- `py:tests/models/test_cookies.py:test_cookies_with_domain:test` at [tests/models/test_cookies.py:33](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L33): calls it directly at [tests/models/test_cookies.py:35](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L35), [tests/models/test_cookies.py:36](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L36)
- `py:tests/models/test_cookies.py:test_cookies_with_domain_and_path:test` at [tests/models/test_cookies.py:45](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L45): calls it directly at [tests/models/test_cookies.py:47](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L47), [tests/models/test_cookies.py:48](https://github.com/encode/httpx/blob/b5addb64f0161ff6bfe94c124ef76f6a1fba5254/tests/models/test_cookies.py#L48)

Check: each path is real; no other test reaches the target in two steps.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## remeda-lookup-002 (lookup, dev, scip)

**Question:** Where is the function `uniqueImplementation` defined?

**Answer:** `ts:packages/remeda/src/unique.ts:uniqueImplementation:function` at [packages/remeda/src/unique.ts:44](https://github.com/remeda/remeda/blob/ec4d3f5dad1105c522c83ec4877e04937cb2405e/packages/remeda/src/unique.ts#L44)

Definitions named `uniqueImplementation` in the repo: 1.

Check: the linked line defines the symbol the question names.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## requests-multihop-002 (multihop, dev, scip)

**Question:** Which test functions reach `merge_environment_settings` (defined in `src/requests/sessions.py`) directly or through exactly one intermediate function?

**Notes:** template: tests-reach-1. Intermediates are non-test functions, methods, classes.

**Target:** `py:src/requests/sessions.py:Session.merge_environment_settings:method` at [src/requests/sessions.py:831](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/sessions.py#L831)

**Answer (tests), with the path to the target:**

- `py:tests/test_requests.py:TestRequests.test_env_cert_bundles:test` at [tests/test_requests.py:1018](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L1018): calls it directly at [tests/test_requests.py:1021](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L1021)
- `py:tests/test_requests.py:TestRequests.test_proxy_authorization_preserved_on_request:test` at [tests/test_requests.py:653](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L653): calls `py:src/requests/sessions.py:Session.request:method` at [tests/test_requests.py:657](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L657), which calls it at [src/requests/sessions.py:641](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/sessions.py#L641)
- `py:tests/test_requests.py:TestRequests.test_request_with_bytestring_host:test` at [tests/test_requests.py:1236](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L1236): calls `py:src/requests/sessions.py:Session.request:method` at [tests/test_requests.py:1238](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L1238), which calls it at [src/requests/sessions.py:641](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/sessions.py#L641)
- `py:tests/test_requests.py:TestRequests.test_respect_proxy_env_on_request:test` at [tests/test_requests.py:647](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L647): calls `py:src/requests/sessions.py:Session.request:method` at [tests/test_requests.py:651](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/tests/test_requests.py#L651), which calls it at [src/requests/sessions.py:641](https://github.com/psf/requests/blob/6e83187b8feb273ed4c6cdab5efd8d54901dfab3/src/requests/sessions.py#L641)

Check: each path is real; no other test reaches the target in two steps.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## requests-unanswerable-002 (unanswerable, test, human)

**Question:** What is the median response time of the requests project's production deployment, according to the maintainers' monitoring dashboards?

**Notes:** Outside the code: no file in the repo can answer this.

Check: nothing in the repository could answer this; a refusal is right.

- [ ] Matches the code at the pinned commit
- Auditor notes:

## zod-callers-002 (callers, dev, scip)

**Question:** Which functions, methods, or tests call `isValidCreditCard` (defined in `packages/zod/src/v4/core/schemas.ts`)?

**Target:** `ts:packages/zod/src/v4/core/schemas.ts:isValidCreditCard:function` at [packages/zod/src/v4/core/schemas.ts:1114](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L1114)

**Answer (callers), with the reference lines:**

- `ts:packages/zod/src/v4/core/compile.ts:generateStringFormatCheck:function` at [packages/zod/src/v4/core/compile.ts:712](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L712): [packages/zod/src/v4/core/compile.ts:742](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L742)
- `ts:packages/zod/src/v4/core/schemas.ts:$ZodCreditCard:function` at [packages/zod/src/v4/core/schemas.ts:1128](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L1128): [packages/zod/src/v4/core/schemas.ts:1135](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L1135)

Every line that mentions `isValidCreditCard` (4):

| Line | Classified as | Text |
|---|---|---|
| [packages/zod/src/v4/core/compile.ts:11](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L11) | import (not a call) | `isValidCreditCard,` |
| [packages/zod/src/v4/core/compile.ts:742](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L742) | counted: caller `ts:packages/zod/src/v4/core/compile.ts:generateStringFormatCheck:function` | `const validator = addConstant(ctx, isValidCreditCard);` |
| [packages/zod/src/v4/core/schemas.ts:1114](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L1114) | definition | `export function isValidCreditCard(input: string): boolean {` |
| [packages/zod/src/v4/core/schemas.ts:1135](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L1135) | counted: caller `ts:packages/zod/src/v4/core/schemas.ts:$ZodCreditCard:function` | `if (isValidCreditCard(payload.value)) return;` |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [ ] Matches the code at the pinned commit
- Auditor notes:

## zod-callers-004 (callers, test, scip)

**Question:** Which functions, methods, or tests call `$ZodLiteral` (defined in `packages/zod/src/v4/core/schemas.ts`)?

**Target:** `ts:packages/zod/src/v4/core/schemas.ts:$ZodLiteral:function` at [packages/zod/src/v4/core/schemas.ts:3563](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L3563)

**Answer (callers), with the reference lines:**

- `ts:packages/zod/src/v4/classic/schemas.ts:ZodLiteral:function` at [packages/zod/src/v4/classic/schemas.ts:2138](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L2138): [packages/zod/src/v4/classic/schemas.ts:2139](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L2139)
- `ts:packages/zod/src/v4/classic/tests/assignability.test.ts:assignability:test` at [packages/zod/src/v4/classic/tests/assignability.test.ts:6](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/assignability.test.ts#L6): [packages/zod/src/v4/classic/tests/assignability.test.ts:95](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/assignability.test.ts#L95)
- `ts:packages/zod/src/v4/core/api.ts:_literal:function` at [packages/zod/src/v4/core/api.ts:1442](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1442): [packages/zod/src/v4/core/api.ts:1432](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1432), [packages/zod/src/v4/core/api.ts:1435](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1435), [packages/zod/src/v4/core/api.ts:1437](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1437) (+2 more)
- `ts:packages/zod/src/v4/core/json-schema-processors.ts:literalProcessor:function` at [packages/zod/src/v4/core/json-schema-processors.ts:175](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/json-schema-processors.ts#L175): [packages/zod/src/v4/core/json-schema-processors.ts:175](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/json-schema-processors.ts#L175)
- `ts:packages/zod/src/v4/mini/schemas.ts:ZodMiniLiteral:function` at [packages/zod/src/v4/mini/schemas.ts:1407](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/schemas.ts#L1407): [packages/zod/src/v4/mini/schemas.ts:1410](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/schemas.ts#L1410)
- `ts:packages/zod/src/v4/mini/tests/assignability.test.ts:assignability:test` at [packages/zod/src/v4/mini/tests/assignability.test.ts:5](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/tests/assignability.test.ts#L5): [packages/zod/src/v4/mini/tests/assignability.test.ts:70](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/tests/assignability.test.ts#L70)

Every line that mentions `$ZodLiteral` (20):

| Line | Classified as | Text |
|---|---|---|
| [packages/zod/src/v4/classic/schemas.ts:2132](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L2132) | not counted (check) | `core.$ZodLiteral<T> {` |
| [packages/zod/src/v4/classic/schemas.ts:2139](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/schemas.ts#L2139) | counted: caller `ts:packages/zod/src/v4/classic/schemas.ts:ZodLiteral:function` | `core.$ZodLiteral.init(inst, def);` |
| [packages/zod/src/v4/classic/tests/assignability.test.ts:94](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/assignability.test.ts#L94) | not counted (check) | `// $ZodLiteral` |
| [packages/zod/src/v4/classic/tests/assignability.test.ts:95](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/classic/tests/assignability.test.ts#L95) | counted: caller `ts:packages/zod/src/v4/classic/tests/assignability.test.ts:assignability:test` | `z.literal("example") satisfies z.core.$ZodLiteral;` |
| [packages/zod/src/v4/core/api.ts:1430](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1430) | not counted (check) | `export type $ZodLiteralParams = TypeParams<schemas.$ZodLiteral, "values">;` |
| [packages/zod/src/v4/core/api.ts:1432](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1432) | counted: caller `ts:packages/zod/src/v4/core/api.ts:_literal:function` | `Class: util.SchemaClass<schemas.$ZodLiteral>,` |
| [packages/zod/src/v4/core/api.ts:1435](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1435) | counted: caller `ts:packages/zod/src/v4/core/api.ts:_literal:function` | `): schemas.$ZodLiteral<T[number]>;` |
| [packages/zod/src/v4/core/api.ts:1437](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1437) | counted: caller `ts:packages/zod/src/v4/core/api.ts:_literal:function` | `Class: util.SchemaClass<schemas.$ZodLiteral>,` |
| [packages/zod/src/v4/core/api.ts:1440](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1440) | counted: caller `ts:packages/zod/src/v4/core/api.ts:_literal:function` | `): schemas.$ZodLiteral<T>;` |
| [packages/zod/src/v4/core/api.ts:1442](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/api.ts#L1442) | counted: caller `ts:packages/zod/src/v4/core/api.ts:_literal:function` | `export function _literal(Class: util.SchemaClass<schemas.$ZodLiteral>, value: any, params:` |
| [packages/zod/src/v4/core/compile.ts:1502](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L1502) | not counted (check) | `// '$ZodLiteral' matches with 'values.has', i.e. SameValueZero, so NaN matches itself. 'x ` |
| [packages/zod/src/v4/core/json-schema-processors.ts:175](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/json-schema-processors.ts#L175) | counted: caller `ts:packages/zod/src/v4/core/json-schema-processors.ts:literalProcessor:function` | `export const literalProcessor: Processor<schemas.$ZodLiteral> = (schema, ctx, json, params` |
| [packages/zod/src/v4/core/schemas.ts:3542](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L3542) | not counted (check) | `//////////      $ZodLiteral      //////////` |
| [packages/zod/src/v4/core/schemas.ts:3559](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L3559) | not counted (check) | `export interface $ZodLiteral<T extends util.Literal = util.Literal> extends $ZodType {` |
| [packages/zod/src/v4/core/schemas.ts:3563](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L3563) | definition | `export const $ZodLiteral: core.$constructor<$ZodLiteral> = /*@__PURE__*/ core.$constructor` |
| [packages/zod/src/v4/core/schemas.ts:3564](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L3564) | not counted (check) | `"$ZodLiteral",` |
| [packages/zod/src/v4/core/schemas.ts:4981](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/schemas.ts#L4981) | not counted (check) | `\| $ZodLiteral` |
| [packages/zod/src/v4/mini/schemas.ts:1410](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/schemas.ts#L1410) | counted: caller `ts:packages/zod/src/v4/mini/schemas.ts:ZodMiniLiteral:function` | `core.$ZodLiteral.init(inst, def);` |
| [packages/zod/src/v4/mini/tests/assignability.test.ts:69](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/tests/assignability.test.ts#L69) | not counted (check) | `// $ZodLiteral` |
| [packages/zod/src/v4/mini/tests/assignability.test.ts:70](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/mini/tests/assignability.test.ts#L70) | counted: caller `ts:packages/zod/src/v4/mini/tests/assignability.test.ts:assignability:test` | `z.literal("example") satisfies z.core.$ZodLiteral;` |

Check: every counted line is a non-import reference inside its caller, and every line marked 'not counted' is not a call to this target (a different symbol with the same name, a comment, a string, or module-level code).

- [ ] Matches the code at the pinned commit
- Auditor notes:

## zod-lookup-002 (lookup, test, scip)

**Question:** Where is the function `generateLiteralCheck` defined?

**Answer:** `ts:packages/zod/src/v4/core/compile.ts:generateLiteralCheck:function` at [packages/zod/src/v4/core/compile.ts:1490](https://github.com/colinhacks/zod/blob/c7ec94d309ee8acb25113e3db738b3e03816a424/packages/zod/src/v4/core/compile.ts#L1490)

Definitions named `generateLiteralCheck` in the repo: 1.

Check: the linked line defines the symbol the question names.

- [ ] Matches the code at the pinned commit
- Auditor notes:
