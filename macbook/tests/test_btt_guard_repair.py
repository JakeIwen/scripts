"""Pure repair planning tests; no BetterTouchTool calls."""
from copy import deepcopy
import unittest

from macbook.bettertouchtool.btt_guard.model import FindingKind
from macbook.bettertouchtool.btt_guard.repair import plan_repair


def state(uuid, parent=None, trigger_type=773, **changes):
    value = dict(uuid=uuid, parent=parent, preset='preset',
                 trigger_type=trigger_type, enabled=True, order=0,
                 action_type=366, action_category=0, payload={}, config={},
                 app_scope=[])
    value.update(changes)
    return value


def snapshot(records, findings=None, presets=None):
    return dict(schema_version=1, captured_at=1.0, btt_version='6.885',
                database_name='fixture', roots=['root'],
                presets={'preset': True} if presets is None else presets,
                records={record['uuid']: record for record in records},
                findings=findings or [])


def definitions():
    return {
        'root': {'BTTUUID': 'root', 'BTTTriggerType': 767,
                 'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                 'BTTTriggerBelongsToPreset': 'Master', 'BTTOrder': 0},
        'item': {'BTTUUID': 'item', 'BTTTriggerType': 773,
                 'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                 'BTTTriggerParentUUID': 'root', 'BTTOrder': 0},
        'action': {'BTTUUID': 'action', 'BTTTriggerType': -1,
                   'BTTTriggerParentUUID': 'item', 'BTTOrder': 0,
                   'BTTPredefinedActionType': 206},
    }


class RepairPlannerTests(unittest.TestCase):
    def test_missing_tree_is_created_parent_first_with_inherited_action_class(self):
        expected = snapshot([
            state('root', trigger_type=767),
            state('item', parent='root'),
            state('action', parent='item', trigger_type=-1, action_type=206),
        ])
        current = snapshot([], findings=[{
            'kind': FindingKind.MISSING.value,
            'uuid': 'root',
            'detail': 'configured root missing',
        }])
        plan = plan_repair(expected, current, definitions(), 'checkpoint-1')
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual([operation['uuid'] for operation in plan['operations']],
                         ['root', 'item', 'action'])
        self.assertTrue(all(operation['mode'] == 'create'
                            for operation in plan['operations']))
        action = plan['operations'][2]['definition']
        self.assertEqual(action['BTTTriggerClass'], 'BTTTriggerTypeFloatingMenu')
        self.assertEqual(action['BTTTriggerParentUUID'], 'item')

    def test_detached_exact_record_is_reattached_but_owned_move_conflicts(self):
        root = state('root', trigger_type=767)
        wanted = state('item', parent='root')
        expected = snapshot([root, wanted])
        detached = snapshot([root, {**wanted, 'parent': None}])
        plan = plan_repair(expected, detached, definitions(), 'checkpoint-2')
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual([(item['uuid'], item['mode'], item['parent'])
                          for item in plan['operations']], [('item', 'reattach', 'root')])

        moved = snapshot([root, {**wanted, 'parent': 'other'}])
        plan = plan_repair(expected, moved, definitions(), 'checkpoint-3')
        self.assertEqual(plan['operations'], [])
        self.assertEqual(plan['conflicts'][0]['kind'], FindingKind.REPARENTED.value)

    def test_detached_record_with_other_drift_is_conflict_not_partial_repair(self):
        wanted = state('action', parent='item', trigger_type=-1,
                       action_type=206, payload={'launch_path': 'private old command'},
                       config={'BTTMenuVisibility': 1})
        actual = deepcopy(wanted)
        actual.update(parent=None, enabled=False, order=1, action_type=248,
                      payload={'launch_path': 'private new command'},
                      config={'BTTMenuVisibility': 2})
        plan = plan_repair(snapshot([wanted]), snapshot([actual]), definitions(), 'checkpoint-4')
        self.assertEqual(plan['operations'], [])
        kinds = {item['kind'] for item in plan['conflicts']}
        self.assertTrue({FindingKind.REPARENTED.value, FindingKind.DISABLED.value,
                         FindingKind.ACTION_CHANGED.value,
                         FindingKind.CONFIG_CHANGED.value} <= kinds)
        self.assertNotIn(FindingKind.ORDER_CHANGED.value, kinds)
        self.assertNotIn('private old command', str(plan['conflicts']))
        self.assertNotIn('private new command', str(plan['conflicts']))

    def test_missing_definition_preset_and_ambiguous_dependency_are_conflicts(self):
        expected = snapshot([state('root', trigger_type=767)])
        current = snapshot([], presets={}, findings=[{
            'kind': FindingKind.DEPENDENCY_CHANGED.value,
            'uuid': 'source',
            'detail': 'named-trigger dependency ambiguous',
        }])
        plan = plan_repair(expected, current, {}, 'checkpoint-5')
        kinds = {item['kind'] for item in plan['conflicts']}
        self.assertTrue({FindingKind.CHECKPOINT_INVALID.value, FindingKind.MISSING.value,
                         FindingKind.DEPENDENCY_CHANGED.value} <= kinds)

    def test_unresolved_dependency_is_allowed_only_with_missing_creations(self):
        finding = {'kind': FindingKind.DEPENDENCY_CHANGED.value,
                   'uuid': 'source', 'detail': 'named-trigger dependency unresolved'}
        root = state('root', trigger_type=767)
        source = state('source', parent='root', trigger_type=-1, action_type=248,
                       payload={'launch_path': 'Target'})
        target = state('target', trigger_type=643, action_type=206,
                       payload={'gesture_config': 'Target'})
        target['app_scope'] = ['BT.G']
        expected = snapshot([root, source, target])
        current = snapshot([root, source], findings=[finding])
        defs = definitions() | {
            'source': {'BTTUUID': 'source', 'BTTTriggerType': -1,
                       'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                       'BTTTriggerParentUUID': 'root', 'BTTPredefinedActionType': 248},
            'target': {'BTTUUID': 'target', 'BTTTriggerType': 643,
                       'BTTTriggerClass': 'BTTTriggerTypeOtherTriggers',
                       'BTTTriggerBelongsToPreset': 'Master',
                       'BTTPredefinedActionType': 206},
        }
        plan = plan_repair(expected, current, defs, 'checkpoint-6')
        self.assertNotIn(finding, plan['conflicts'])
        self.assertEqual([operation['uuid'] for operation in plan['operations']], ['target'])

        unrelated = state('unrelated', parent='root')
        expected = snapshot([root, source, unrelated])
        current = snapshot([root, source], findings=[finding])
        defs['unrelated'] = {'BTTUUID': 'unrelated', 'BTTTriggerType': 773,
                             'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                             'BTTTriggerParentUUID': 'root'}
        plan = plan_repair(expected, current, defs, 'checkpoint-7')
        self.assertIn(finding, plan['conflicts'])

    def test_action_sequence_drift_is_conflict_while_item_order_is_ignored(self):
        action = state('action', parent='item', trigger_type=-1,
                       action_type=206, order=0)
        item = state('item', parent='root', order=4)
        current_action = {**action, 'order': 1}
        current_item = {**item, 'order': 99}
        plan = plan_repair(snapshot([action, item]), snapshot([current_action, current_item]),
                           definitions(), 'checkpoint-8')
        self.assertEqual([(entry['kind'], entry['uuid']) for entry in plan['conflicts']],
                         [(FindingKind.ORDER_CHANGED.value, 'action')])

    def test_app_scope_drift_is_configuration_conflict(self):
        wanted = state('root', trigger_type=767, app_scope=['BT.G'])
        actual = {**wanted, 'app_scope': []}
        plan = plan_repair(snapshot([wanted]), snapshot([actual]),
                           definitions(), 'checkpoint-app-scope')
        self.assertEqual(plan['operations'], [])
        self.assertEqual(plan['conflicts'], [{
            'kind': FindingKind.CONFIG_CHANGED.value,
            'uuid': 'root',
            'detail': 'fields: app_scope',
        }])

    def test_detached_second_action_is_reattached_despite_foreign_group_rank(self):
        root = state('root', trigger_type=767)
        item = state('item', parent='root')
        first = state('action-a', parent='item', trigger_type=-1,
                      action_type=206, order=0)
        second = state('action-b', parent='item', trigger_type=-1,
                       action_type=206, order=1)
        current_second = {**second, 'parent': None, 'order': 0}
        expected = snapshot([root, item, first, second])
        current = snapshot([root, item, first, current_second])
        defs = definitions() | {
            'action-a': {'BTTUUID': 'action-a', 'BTTTriggerType': -1,
                         'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                         'BTTTriggerParentUUID': 'item', 'BTTPredefinedActionType': 206,
                         'BTTOrder': 0},
            'action-b': {'BTTUUID': 'action-b', 'BTTTriggerType': -1,
                         'BTTTriggerClass': 'BTTTriggerTypeFloatingMenu',
                         'BTTTriggerParentUUID': 'item', 'BTTPredefinedActionType': 206,
                         'BTTOrder': 1},
        }
        plan = plan_repair(expected, current, defs, 'checkpoint-orphan-order')
        self.assertEqual(plan['conflicts'], [])
        self.assertEqual([(operation['uuid'], operation['mode'])
                          for operation in plan['operations']], [('action-b', 'reattach')])


if __name__ == '__main__':
    unittest.main()
