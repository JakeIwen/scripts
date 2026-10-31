"""Metering mutations cross only the stable CLI; the panel never writes policy."""
from cli_runner import json_result
from panel_contracts import ApiError
from panel_paths import ROOT
from wire_validation import field, object_fields

COMMAND = (str(ROOT / 'macbook/metered/mac-metered'),)


def payload(value):
    object_fields(value, ('config', 'policy', 'state', 'registry', 'pi_state', 'pending',
                         'sync', 'fresh', 'config_cached', 'low_data_mode'), status=502)
    for key in ('config', 'policy', 'state', 'sync', 'low_data_mode'):
        field(value, key, dict)
    for key in ('registry', 'pending'):
        field(value, key, list)
    field(value, 'pi_state', dict, nullable=True)
    field(value, 'fresh', bool); field(value, 'config_cached', bool)
    return value


def policy_args(body):
    action = field(body, 'action', str, status=400)
    fields = {'mode': ('value', 'duration'), 'approve': ('consumer', 'duration'),
              'revoke': ('consumer',), 'exempt': ('consumer', 'enabled')}
    if action not in fields:
        raise ApiError(400, 'Unknown policy operation')
    object_fields(body, ('action', 'revision', *fields[action]))
    revision = field(body, 'revision', int, status=400)
    values = {key: field(body, key, str, status=400) for key in fields[action]}
    argv = {
        'mode': lambda: ['mode', values['value']] +
            (['--for', values['duration']] if values['duration'] else []),
        'approve': lambda: ['approve', values['consumer'], values['duration']],
        'revoke': lambda: ['revoke', values['consumer']],
        'exempt': lambda: ['exempt', values['consumer'], values['enabled']],
    }[action]()
    return [*argv, '--revision', str(revision)]


def mutation_args(body):
    action = field(body, 'action', str, status=400)
    if action in ('ssid-add', 'ssid-remove'):
        expected = ('action', 'ssid') + (('revision',) if action == 'ssid-remove' else ())
        object_fields(body, expected)
        ssid = field(body, 'ssid', str, status=400)
        args = ['ssid', 'add' if action == 'ssid-add' else 'remove']
        if action == 'ssid-remove':
            args += ['--revision', str(field(body, 'revision', int, status=400))]
        return [*args, '--', ssid]
    if action == 'sync':
        object_fields(body, ('action',))
        return ['sync']
    if action == 'low-data-mode':
        object_fields(body, ('action', 'enabled', 'revision', 'confirmed'))
        enabled = field(body, 'enabled', bool, status=400)
        revision = field(body, 'revision', str, status=400)
        if field(body, 'confirmed', bool, status=400) is not True:
            raise ApiError(400, 'Explicit Low Data Mode confirmation is required')
        return ['low-data-mode', 'on' if enabled else 'off', '--revision', revision, '--confirm']
    if action == 'restore-low-data-mode':
        object_fields(body, ('action', 'confirmed'))
        if field(body, 'confirmed', bool, status=400) is not True:
            raise ApiError(400, 'Explicit flag restore confirmation is required')
        return ['low-data-mode', 'restore', '--confirm']
    return policy_args(body)


class MeteredAdapter:
    def __init__(self, runner):
        self.runner = runner

    def read(self):
        return payload(json_result(self.runner.run((*COMMAND, 'status', '--json'))))

    def mutate(self, body):
        result = self.runner.run((*COMMAND, *mutation_args(body)), timeout=90)
        if result.returncode in (2, 75):
            value = json_result(result, allowed=(2, 75))
            raise ApiError(409 if result.returncode == 75 else 400, value.get('error', 'Metering edit failed'))
        json_result(result)
        return self.read()
