"""The maintenance front door is read-only by default and keeps archives inert."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import subprocess
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
                     ['escape'],['repair','rps'],['repair','rps','--with-escape'],['repair','sizes'],['repair','persistence'],['repair','paths']]:
            with self.subTest(argv=argv):
                command=module.command_for(module.parser().parse_args(argv))
                self.assertIn('--inspect',command)
                self.assertFalse(any('one_off_fixes' in arg for arg in command))

    def test_explicit_apply_preserves_feature_scope(self):
        args=module.parser().parse_args(['style','status','--apply'])
        command=module.command_for(args)
        self.assertIn('--status-style',command);self.assertNotIn('--inspect',command)
        command=module.command_for(module.parser().parse_args(['notes','--labels-only','--apply']))
        self.assertEqual(command[-1],'--labels-only')
        command=module.command_for(module.parser().parse_args(['repair','paths','--apply']))
        self.assertEqual(command[-1],'--apply')
        self.assertTrue(command[-2].endswith('repair_script_paths.py'))

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

    def test_active_sources_do_not_import_archived_helpers(self):
        for filename in ['stabilize_media.js','port_media_icons.py','repair_rps.py','install_menu_escape.py']:
            source=(DIRECTORY/filename).read_text()
            self.assertNotIn('one_off_fixes/',source)
            self.assertNotIn("'repair_media_controls.js'",source)
            self.assertNotIn('from stabilize_media import',source)
            self.assertNotIn('from repair_rps import backup_configuration',source)

    def test_historical_shell_and_import_generators_are_inert_by_default(self):
        commands = [[ '/bin/zsh', str(path)] for path in (DIRECTORY/'one_off_fixes').glob('*.zsh')]
        commands += [[sys.executable,'-B',str(DIRECTORY/'one_off_fixes'/name)] for name in
                     ['2026-07-19-CRASH-RISK-recent-notes-submenu.py','2026-07-25-build-media-vertical-dropdowns.py']]
        for command in commands:
            with self.subTest(command=command):
                # Explicitly clear the historical opt-in for this safety check.
                import os
                environment=dict(os.environ);environment.pop('BTT_RUN_HISTORICAL',None)
                result=subprocess.run(command,env=environment,capture_output=True,text=True,timeout=10)
                self.assertNotEqual(result.returncode,0)
                self.assertTrue('ARCHIVED' in result.stderr or 'CRASH RISK' in result.stderr,result.stderr)


if __name__=='__main__':unittest.main()
