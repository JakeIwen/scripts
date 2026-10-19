"""Declaration-driven settings; persistence belongs entirely to tool_settings."""
from panel_contracts import ApiError, SettingsPayload
from wire_validation import field, object_fields


class SettingsAdapter:
    def __init__(self, settings=None):
        if settings is None:
            import tool_settings
            settings = tool_settings
        self.settings = settings

    def read(self) -> SettingsPayload:
        return {'tools': [{'declaration': self.settings.load_declaration(tool).as_dict(),
                           'state': self.settings.read_state(tool).as_dict()}
                          for tool in self.settings.declaration_tools()]}

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
