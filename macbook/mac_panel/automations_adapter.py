"""Declaration-driven automation status and commands with no panel-owned state."""
from background_tasks import BackgroundTasks
from cli_runner import checked, json_result
from panel_contracts import Section, ApiError, AutomationRecord, AutomationsPayload, Reply
from wire_validation import confirm, field, object_fields


def _declaration_error(tool, error) -> AutomationRecord:
    return AutomationRecord(
        tool=tool,
        title=tool,
        description='',
        service=None,
        settings=None,
        actions=[],
        status=None,
        error=str(error),
    )


def _last_output_line(stdout):
    return next((line.strip() for line in reversed(stdout.splitlines()) if line.strip()), 'Done')


class AutomationsAdapter:
    def __init__(self, runner, automations=None, tasks=None):
        if automations is None:
            import tool_automation
            automations = tool_automation
        self.tasks = tasks or BackgroundTasks()
        self.runner = runner
        self.automations = automations

    def _read_one(self, tool) -> AutomationRecord:
        try:
            declaration = self.automations.load_declaration(tool)
        except self.automations.AutomationError as exc:
            return _declaration_error(tool, exc)
        record = AutomationRecord(**declaration.as_dict(), status=None, error=None)
        try:
            value = json_result(self.runner.run(declaration.status.argv,
                                                timeout=declaration.status.timeout))
            record['status'] = self.automations.validate_status(value)
        except (ApiError, self.automations.AutomationError) as exc:
            record['error'] = str(exc)
        return record

    def read(self) -> AutomationsPayload:
        tools = sorted(self.automations.declaration_tools())
        return AutomationsPayload(tasks=self.tasks.list(Section.AUTOMATIONS), automations=[self._read_one(tool) for tool in tools])

    def action(self, body):
        object_fields(body, ('tool', 'action'), ('confirmed',))
        tool = field(body, 'tool', str, status=400)
        action_name = field(body, 'action', str, status=400)
        if tool not in self.automations.declaration_tools():
            raise ApiError(400, 'Unknown automation')
        try:
            declaration = self.automations.load_declaration(tool)
        except self.automations.AutomationError as exc:
            raise ApiError(502, str(exc)) from None
        action = declaration.action(action_name)
        if action is None:
            raise ApiError(400, 'Unknown automation action')
        if action.confirm:
            confirm(body)
        elif 'confirmed' in body:
            field(body, 'confirmed', bool, status=400)
        if action.asynchronous:
            task = self.tasks.start(Section.AUTOMATIONS, f'automation:{tool}:{action_name}', lambda: self.runner.run(
                action.command.argv, timeout=action.command.timeout))
            return Reply(task, 202)
        result = checked(self.runner.run(action.command.argv, timeout=action.command.timeout))
        return {'message': _last_output_line(result.stdout)}
