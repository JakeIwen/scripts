"""Declaration-driven settings; persistence belongs entirely to tool_settings."""
from panel_contracts import ApiError, SettingsPayload, SettingsToolRecord
from wire_validation import field, object_fields


def _declaration_error(tool, error) -> SettingsToolRecord:
    return SettingsToolRecord(tool=tool, declaration=None, state=None, error=str(error))


class SettingsAdapter:
    def __init__(self, settings=None):
        if settings is None:
            import tool_settings
            settings = tool_settings
        self.settings = settings

    def _read_one(self, tool) -> SettingsToolRecord:
        # One unreadable declaration (malformed, declared twice, a dangling link) becomes
        # that tool's error card; the other forms stay usable.
        try:
            declaration = self.settings.load_declaration(tool)
            state = self.settings.read_state(tool)
        except self.settings.SettingsError as exc:
            return _declaration_error(tool, exc)
        return SettingsToolRecord(tool=tool, declaration=declaration.as_dict(),
                                  state=state.as_dict(), error=None)

    def read(self) -> SettingsPayload:
        return SettingsPayload(tools=[self._read_one(tool) for tool in self.settings.declaration_tools()])

    def save(self, body):
        object_fields(body, ('tool', 'values', 'revision'))
        tool = field(body, 'tool', str, status=400)
        values = field(body, 'values', dict, status=400)
        revision = field(body, 'revision', str, nullable=True, status=400)
        if tool not in self.settings.declaration_tools():
            raise ApiError(400, 'Unknown settings tool')
        try:
            return self.settings.save(tool, values, expected_revision=revision).as_dict()
        except self.settings.SettingsConflict as exc:
            raise ApiError(409, str(exc)) from None
        except self.settings.SettingsError as exc:
            raise ApiError(400, str(exc)) from None
