# Tool settings

Each configurable background tool has one declaration in `<tool>.json`. The tool
and Mac Panel both load that declaration through `shared/python/tool_settings.py`,
so defaults, validation bounds, labels, and help text stay in one place.

Tools that live outside this repository declare their settings in
`~/.config/jacobr/declarations/<tool>.json` instead (same format; usually a symlink
into the tool's own checkout, which is followed). The tool list, CLI and panel merge
both directories; a tool declared in both is an error until one copy is removed.

A declaration is a JSON object with `tool`, `title`, `description`, and an ordered
`settings` array. Every setting has `name`, `type`, `default`, and `description`.
Supported types are `integer`, `number`, `boolean`, and `choice`. Numeric settings
may have `minimum` and `maximum`; choice settings have a non-empty `choices` list.
`label` (form wording; `name` stays the stored key), `step` and `unit` are optional
UI hints, and `step` does not constrain validation.

Tools read effective values with `tool_settings.load("<tool>")`; long-running tools
use `tool_settings.Reloader` to pick up edits. The panel uses the same module to
render declarations, read revisions, validate updates, and save them atomically.
Only non-default overrides are stored in `~/.config/jacobr/<tool>.json`; declaration
files never contain a user's saved values.
