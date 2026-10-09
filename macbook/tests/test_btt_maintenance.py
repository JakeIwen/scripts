"""The maintenance front door is read-only by default."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

DIRECTORY=Path(__file__).resolve().parents[1]/'bettertouchtool'
sys.path.insert(0,str(DIRECTORY))
spec=importlib.util.spec_from_file_location('btt_maintenance',DIRECTORY/'btt.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
sys.path.pop(0)


class MaintenanceTests(unittest.TestCase):
    def test_every_change_command_defaults_to_inspection(self):
        for argv in [['style'],['style','transport'],['style','status'],['status'],['notes'],['notes','--labels-only'],
                     ['escape'],['repair','rps'],['repair','rps','--with-escape'],['repair','sizes'],['repair','persistence'],['repair','paths'],['repair','actions'],['repair','dependencies']]:
            with self.subTest(argv=argv):
                command=module.command_for(module.parser().parse_args(argv))
                self.assertIn('--inspect',command)

    def test_explicit_apply_preserves_feature_scope(self):
        args=module.parser().parse_args(['style','status','--apply'])
        command=module.command_for(args)
        self.assertIn('--status-style',command);self.assertNotIn('--inspect',command)
        command=module.command_for(module.parser().parse_args(['notes','--labels-only','--apply']))
        self.assertEqual(command[-1],'--labels-only')
        command=module.command_for(module.parser().parse_args(['repair','paths','--apply']))
        self.assertEqual(command[-1],'--apply')
        self.assertTrue(command[-2].endswith('repair_script_paths.py'))
        command=module.command_for(module.parser().parse_args(['repair','actions','--apply']))
        self.assertEqual(command[-1],'--apply')
        self.assertTrue(command[-2].endswith('repair_media_actions.py'))
        command=module.command_for(module.parser().parse_args(['repair','dependencies','--apply']))
        self.assertIn('--dependencies-only',command)
        self.assertEqual(command[-1],'--apply')

    def test_guard_uses_package_entry_without_import_path_shims(self):
        with patch.object(module.subprocess,'run') as runner:
            runner.return_value.returncode=0
            self.assertEqual(module.main(['guard','--state-dir','/private/tmp/fixture','audit']),0)
        command=runner.call_args.args[0]
        self.assertIn('macbook.bettertouchtool.btt_guard',command)
        self.assertEqual(command[-3:],['--state-dir','/private/tmp/fixture','audit'])
        self.assertEqual(runner.call_args.kwargs['cwd'],DIRECTORY.parents[1])

    def test_deleted_media_recovery_requires_explicit_apply(self):
        command=module.command_for(module.parser().parse_args(['restore-media']))
        self.assertNotIn('--apply',command)
        self.assertTrue(command[-1].endswith('restore_media.py'))
        command=module.command_for(module.parser().parse_args(['restore-media','--apply']))
        self.assertEqual(command[-1],'--apply')
        command=module.command_for(module.parser().parse_args(['restore-media','--resume','--apply']))
        self.assertEqual(command[-2:],['--resume','--apply'])

    def test_invalid_repair_option_combinations_are_rejected(self):
        for argv in [['repair','sizes','--with-escape'],['repair','rps','--full-height-dropdowns']]:
            with self.assertRaises(ValueError):module.command_for(module.parser().parse_args(argv))

    def test_restore_requires_explicit_apply_and_does_not_shell_interpolate(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            module.parser().parse_args(['restore-sizes','backup.json'])
        args=module.parser().parse_args(['restore-sizes','/tmp/a; echo unsafe.json','--apply'])
        command=module.command_for(args)
        self.assertEqual(command[-1],str(Path('/tmp/a; echo unsafe.json').resolve()))
        self.assertEqual(command[-2],'--restore')

    def test_no_arguments_only_prints_help(self):
        with patch.object(module.subprocess,'run') as runner,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main([]),0)
        runner.assert_not_called()

    def test_retained_runtime_and_feature_entry_points_exist(self):
        for filename in ['play_status.js','recent_notes_content_script.js','btt_widget_spotify.scpt','btt_backup.zsh',
                         'install_performance_audio_menu.py','install_rhythm_menu.py','install_bpm_menu.py',
                         'install_convert_video_menu.py','install_strip_metadata_menu.py']:
            self.assertTrue((DIRECTORY/filename).is_file(),filename)

if __name__=='__main__':unittest.main()
