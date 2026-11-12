# click flow questions

- Written by: Claude (AI draft from reading the source at the pinned commit; needs owner review)
- Reviewed by: pending

## click-flow-001

**Question:** When a user runs `cli sync --force` against a non-chained `click.Group`, how does Click get from `cli()` to the `sync` subcommand's callback?

**Must cite:**
- `py:src/click/core.py:Command.main:method~3`
- `py:src/click/core.py:Command.make_context:method`
- `py:src/click/core.py:Group.parse_args:method`
- `py:src/click/core.py:Group.invoke:method`
- `py:src/click/core.py:Group.resolve_command:method`
- `py:src/click/core.py:Group.get_command:method`
- `py:src/click/core.py:Command.invoke:method`
- `py:src/click/core.py:Context.invoke:method~3`

**Reference outline:**
1. Calling the group runs `Command.main`, which reads `sys.argv[1:]`, checks for a completion request, then builds a context with `make_context` and calls `invoke` on it.
2. `Command.make_context` creates the group's `Context` and calls the command's `parse_args` inside the context scope.
3. `Group.parse_args` lets the base command parse the group's own options, then stores the first leftover token (the subcommand name) in `ctx._protected_args` and the rest in `ctx.args`.
4. `Group.invoke` joins those back into one argument list and, for a non-chained group, calls `resolve_command`.
5. `Group.resolve_command` takes the first token as the command name and looks it up with `get_command` (retrying with `token_normalize_func` if set), raising `NoSuchCommand` when nothing matches; `Group.get_command` is a lookup in `self.commands`.
6. `Group.invoke` records `invoked_subcommand`, runs the group's own callback through the base `Command.invoke`, then calls the subcommand's `make_context` with the remaining arguments and the group context as parent, which parses `--force`.
7. The subcommand's `Command.invoke` calls `ctx.invoke(self.callback, **ctx.params)`, and `Context.invoke` calls the callback with those keyword arguments; the result goes through the group's result callback, if any.

## click-flow-002

**Question:** For an option declared with `type=int` and `envvar="PORT"` that is not given on the command line, how does Click read the environment variable and turn it into an `int` in `ctx.params`?

**Must cite:**
- `py:src/click/core.py:Command.parse_args:method`
- `py:src/click/core.py:Parameter.handle_parse_result:method`
- `py:src/click/core.py:Option.consume_value:method`
- `py:src/click/core.py:Parameter.consume_value:method`
- `py:src/click/core.py:Option.value_from_envvar:method`
- `py:src/click/core.py:Parameter.process_value:method`
- `py:src/click/core.py:Parameter.type_cast_value:method`
- `py:src/click/types.py:_NumberParamTypeBase.convert:method`

**Reference outline:**
1. When the option is created, `type=int` is mapped to the shared `IntParamType` instance by `types.convert_type`, and `Option` keeps that explicit type.
2. `Command.parse_args` runs the option parser, then calls `handle_parse_result` on every parameter in processing order.
3. `Parameter.handle_parse_result` calls `consume_value` to get a value and its source, records the source on the context, then passes the value to `process_value`.
4. `Option.consume_value` first defers to `Parameter.consume_value`, which finds no parser value and so asks `value_from_envvar`, marking the source as `ENVIRONMENT` when it returns something (default map and default come after).
5. `Option.value_from_envvar` reads the variable through `resolve_envvar_value` (which also supports `auto_envvar_prefix`); for a non-flag option with `nargs=1` it returns the raw string unchanged.
6. `Parameter.process_value` (reached via `Option.process_value`, which only special-cases boolean flags) calls `type_cast_value`, then checks `required` and runs any parameter callback.
7. `type_cast_value` with `nargs=1` calls the type object, whose `__call__` delegates to `convert`; `_NumberParamTypeBase.convert` calls `int(value)` and turns a `ValueError` into a usage error via `fail`.
8. Back in `handle_parse_result`, the converted value is stored in `ctx.params` under the parameter's name.

## click-flow-003

**Question:** When a user passes `--help` to a command, how does Click build the help page text and exit?

**Must cite:**
- `py:src/click/core.py:Command.get_help_option:method`
- `py:src/click/decorators.py:help_option.show_help:function`
- `py:src/click/core.py:Context.get_help:method`
- `py:src/click/core.py:Command.get_help:method`
- `py:src/click/core.py:Command.format_help:method`
- `py:src/click/core.py:Command.format_options:method`
- `py:src/click/core.py:Option.get_help_record:method`
- `py:src/click/formatting.py:HelpFormatter.write_dl:method`

**Reference outline:**
1. `Command.get_params` appends the option returned by `get_help_option`, which builds it once (cached on the command) with the `help_option` decorator from `decorators.py`: an eager boolean flag that does not expose a value and has `show_help` as its callback.
2. Because the help option is eager, it is processed before other parameters during parsing; its value is converted and its callback runs from `process_value`.
3. `show_help` (when the flag is set and parsing is not resilient) echoes `ctx.get_help()` and then calls `ctx.exit()`.
4. `Context.get_help` delegates to `Command.get_help`, which creates a `HelpFormatter` via `ctx.make_formatter`, calls `format_help`, and returns the buffered text.
5. `Command.format_help` writes, in order, the usage line, help text, positional arguments, options, and epilog.
6. `Command.format_options` collects a help record from each non-argument parameter; `Option.get_help_record` returns the option spec and its help, with extras such as env var, default, range, or required in brackets (hidden options return nothing).
7. Inside an "Options" section, `HelpFormatter.write_dl` lays out those records as a two-column definition list, wrapping the description column to the formatter width.
8. `ctx.exit()` raises `Exit`, which `Command.main` catches in standalone mode and turns into `sys.exit` with code 0.

## click-flow-004

**Question:** When Bash asks for tab completion of a group's subcommand name (for example `cli sy<TAB>` with `_CLI_COMPLETE=bash_complete`), how does Click compute and print the candidates?

**Must cite:**
- `py:src/click/core.py:Command._main_shell_completion:method`
- `py:src/click/shell_completion.py:shell_complete:function`
- `py:src/click/shell_completion.py:ShellComplete.complete:method`
- `py:src/click/shell_completion.py:BashComplete.get_completion_args:method`
- `py:src/click/shell_completion.py:ShellComplete.get_completions:method`
- `py:src/click/shell_completion.py:_resolve_context:function`
- `py:src/click/shell_completion.py:_resolve_incomplete:function`
- `py:src/click/core.py:Group.shell_complete:method`

**Reference outline:**
1. `Command.main` calls `_main_shell_completion` before normal parsing; it derives the variable name `_{PROG_NAME}_COMPLETE`, returns if it is unset, and otherwise calls `shell_completion.shell_complete` and exits with its return code.
2. `shell_complete` splits the instruction into shell and action, looks up the completion class with `get_completion_class` (Bash here), and for the `complete` action echoes the result of `complete()`.
3. `ShellComplete.complete` gets the arguments and the incomplete word, calls `get_completions`, and joins one formatted line per item.
4. `BashComplete.get_completion_args` splits `COMP_WORDS`, uses `COMP_CWORD` to separate the finished arguments from the word being completed.
5. `ShellComplete.get_completions` builds the context with `_resolve_context`, picks the object to complete with `_resolve_incomplete`, and calls that object's `shell_complete`.
6. `_resolve_context` makes the root context with `resilient_parsing=True` and walks down through any subcommands already typed, using `resolve_command` and `make_context`, without running callbacks.
7. `_resolve_incomplete` sees that the incomplete word is not an option and no argument is waiting for a value, so it returns the current command (the group).
8. `Group.shell_complete` yields a `CompletionItem` for each visible subcommand whose name starts with the incomplete word (with its short help), then appends whatever the base `Command.shell_complete` adds (option names only when the word starts with a non-alphanumeric character, and siblings from chained parent groups); `BashComplete.format_completion` writes each item as `type,value`.
