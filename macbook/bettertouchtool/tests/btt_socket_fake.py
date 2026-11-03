"""In-memory stand-in for BTT's socket CLI (btt_socket.call), for trigger installer tests."""
import json

from trigger_install import PASSTHROUGH


class FakeBTT:
    """Socket fake with the API's export shape: aliased script key, read-only condition."""
    def __init__(self, export_hook=None):
        self.triggers, self.writes, self.export_hook = {}, [], export_hook

    def __call__(self, command, **parameters):
        if command == 'get_trigger':
            found = self.triggers.get(parameters['uuid'])
            return json.dumps([found] if found else {})
        if command == 'add_new_trigger':
            wanted = json.loads(parameters['json'])
            exported = dict(wanted)
            exported['BTTTriggerConditionsFormatReadOnly'] = exported.pop('BTTTriggerConditionsFormat', None)
            exported.pop(PASSTHROUGH, None)
            data = dict(exported['BTTAdditionalActionData'])
            data['BTTScriptString'] = data.pop('BTTAppleScriptString')
            exported['BTTAdditionalActionData'] = data
            if self.export_hook:
                self.export_hook(exported)
            self.triggers[wanted['BTTUUID']] = exported
            self.writes.append(('add', wanted['BTTUUID'], wanted['BTTEnabled']))
        elif command == 'update_trigger':
            self.triggers[parameters['uuid']].update(json.loads(parameters['json']))
            self.writes.append(('update', parameters['uuid'], json.loads(parameters['json'])['BTTEnabled']))
        elif command == 'delete_trigger':
            del self.triggers[parameters['uuid']]
            self.writes.append(('delete', parameters['uuid'], None))
        return ''
