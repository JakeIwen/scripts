"""Finder paste key: the runtime's pasteboard choices and file names, the trigger
condition, and the installer's trigger definition.

The runtime runs under osascript against a disposable named pasteboard and a
temporary folder: nothing reads or writes the general clipboard or drives Finder.
"""
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

import finder_paste_condition as condition
import install_finder_paste as finder_paste
import trigger_install as triggers

BTTCLI = '/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli'
STAMP = 'Screenshot 2026-01-05 at 07.08.09'

# JavaScript fixtures: a 3x2 bitmap and its encodings.
FIXTURES = r'''
function fixtureBitmap() {
    return $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
        null, 3, 2, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
}
function fixtureEncoded(type) { return fixtureBitmap().representationUsingTypeProperties(type, $({})); }
'''


def png_fixture(width=4, height=3):
    """A valid RGBA PNG built byte by byte, so the saved copy can be compared exactly."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\x00' + b'\xff\x40\x00\xff' * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def png_size(path):
    data = path.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or data[12:16] != b'IHDR':
        raise AssertionError(path.name + ' is not a PNG')
    return struct.unpack('>II', data[16:24])


def run_runtime(fill, call):
    """Runs the `call` expression with `board`/`name` bound to a fresh named pasteboard
    that the `fill` statements prepare; the pasteboard is released afterwards."""
    sources = '\n'.join(Path(path).read_text(encoding='utf-8') for path in finder_paste.RUNTIME)
    script = sources + FIXTURES + '''
(function () {
    const board = $.NSPasteboard.pasteboardWithUniqueName, name = ObjC.unwrap(board.name);
    try {
        board.clearContents;
        %s
        const version = Number(board.changeCount);
        const result = %s;
        return JSON.stringify({result, pasteboardUnchanged: Number(board.changeCount) === version});
    } finally {
        board.releaseGlobally;
    }
})();''' % (fill, call)
    output = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-'], input=script,
                            capture_output=True, text=True, timeout=60)
    if output.returncode:
        raise AssertionError('runtime failed: ' + output.stderr.strip())
    return json.loads(output.stdout)


@unittest.skipUnless(sys.platform == 'darwin', 'the runtime needs macOS AppKit through osascript')
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)

    def paste(self, fill, destination=None):
        options = json.dumps({'destination': str(destination or self.folder)})
        outcome = run_runtime(fill, 'JSON.parse(runFinderPaste(Object.assign({pasteboard: name}, %s)))' % options)
        self.assertTrue(outcome['pasteboardUnchanged'], 'the runtime wrote the pasteboard')
        return outcome['result']

    def saved(self):
        return sorted(path.name for path in self.folder.iterdir())

    def test_copied_files_paste_normally_even_with_their_icon_image(self):
        fill = '''const item = $.NSPasteboardItem.alloc.init;
        item.setStringForType('file:///private/tmp/fixture.txt', 'public.file-url');
        item.setDataForType(fixtureBitmap().TIFFRepresentation, 'public.tiff');
        board.writeObjects($([item]));'''
        self.assertEqual(self.paste(fill), {'ok': True, 'result': 'normal paste'})
        self.assertEqual(self.saved(), [])

    def test_a_screenshot_png_is_saved_byte_for_byte(self):
        with tempfile.NamedTemporaryFile(suffix='.png') as source:
            source.write(png_fixture())
            source.flush()
            # A screenshot carries the same image as TIFF too; the PNG is the one kept.
            outcome = self.paste('''board.setDataForType($.NSData.dataWithContentsOfFile(%s), 'public.png');
            board.setDataForType(fixtureBitmap().TIFFRepresentation, 'public.tiff');''' % json.dumps(source.name))
        [name] = self.saved()
        self.assertEqual(outcome, {'ok': True, 'result': 'saved ' + name})
        self.assertRegex(name, r'^Screenshot \d{4}-\d\d-\d\d at \d\d\.\d\d\.\d\d\.png$')
        self.assertEqual((self.folder / name).read_bytes(), png_fixture())

    def test_other_image_types_are_converted_to_png(self):
        for kind, fill in [('TIFF', "board.setDataForType(fixtureBitmap().TIFFRepresentation, 'public.tiff');"),
                           ('JPEG', "board.setDataForType(fixtureEncoded($.NSBitmapImageFileTypeJPEG), 'public.jpeg');")]:
            with self.subTest(kind=kind):
                outcome = self.paste(fill)
                [name] = self.saved()
                self.assertEqual(outcome, {'ok': True, 'result': 'saved ' + name})
                self.assertEqual(png_size(self.folder / name), (3, 2))
                (self.folder / name).unlink()

    def test_text_pastes_normally(self):
        fill = "board.setStringForType('just text', 'public.utf8-plain-text');"
        self.assertEqual(self.paste(fill), {'ok': True, 'result': 'normal paste'})
        self.assertEqual(self.saved(), [])

    def test_a_missing_folder_stops_without_writing(self):
        missing = self.folder / 'gone'
        outcome = self.paste("board.setDataForType(fixtureBitmap().TIFFRepresentation, 'public.tiff');", missing)
        self.assertFalse(outcome['ok'])
        self.assertIn('does not exist', outcome['error'])
        self.assertEqual(self.saved(), [])

    def test_names_use_local_time_and_never_replace_an_existing_file(self):
        (self.folder / (STAMP + ' 2.png')).write_bytes(b'keep')
        os.symlink(self.folder / 'nowhere', self.folder / (STAMP + ' 3.png'))
        call = '''[1, 2].map(() => ObjC.unwrap($(saveClipboardImage(board, %s,
            new Date(2026, 0, 5, 7, 8, 9))).lastPathComponent))''' % json.dumps(str(self.folder))
        fill = "board.setDataForType(fixtureEncoded($.NSBitmapImageFileTypePNG), 'public.png');"
        self.assertEqual(run_runtime(fill, call)['result'], [STAMP + '.png', STAMP + ' 4.png'])
        self.assertEqual((self.folder / (STAMP + ' 2.png')).read_bytes(), b'keep')
        self.assertEqual(self.saved(), [STAMP + ' 2.png', STAMP + ' 3.png', STAMP + ' 4.png', STAMP + '.png'])


@unittest.skipUnless(sys.platform == 'darwin', 'NSPredicate evaluation needs macOS osascript')
class ConditionTests(unittest.TestCase):
    def test_condition_passes_its_truth_table_in_foundation(self):
        self.assertEqual(condition.truth_table_errors(), [])

    def test_a_condition_that_ignores_text_focus_or_the_app_is_caught(self):
        for loose, expected in [(condition.CONDITION.replace(' AND NOT ', ' AND '), 'condition misses on: file list'),
                                (condition.CONDITION.split(' AND ', 1)[1], 'condition fires on: another app')]:
            with self.subTest(loose=loose), patch.object(condition, 'CONDITION', loose):
                self.assertIn(expected, condition.truth_table_errors())


class DefinitionTests(unittest.TestCase):
    def test_one_disabled_pass_through_cmd_v_trigger_kept_to_finder_by_its_condition(self):
        [trigger] = finder_paste.definitions()
        self.assertEqual((trigger['BTTUUID'], trigger['BTTShortcutKeyCode'], trigger['BTTShortcutModifierKeys']),
                         (finder_paste.TRIGGER, 9, triggers.COMMAND))
        self.assertEqual(trigger['BTTAppBundleIdentifier'], triggers.GLOBAL)
        self.assertEqual((trigger['BTTEnabled'], trigger[triggers.PASSTHROUGH]), (0, True))
        self.assertEqual(trigger['BTTTriggerConditionsFormat'], condition.CONDITION)
        self.assertEqual(trigger['BTTAdditionalActionData']['BTTScriptFunctionToCall'], 'pasteInFinder')

    def test_finder_definition_keeps_the_original_literal_action_script(self):
        # Only the checkout path varies: the action itself is a saved BTT contract.
        expected = r'''async function pasteInFinder() {
    const sources = await Promise.all(["ROOT/text_selection.js", "ROOT/text_menu_paste.js", "ROOT/finder_paste.js"].map(path => readFile(path)));
    const result = JSON.parse(await runJXA(sources.join("\n") + "\nrunFinderPaste({});"));
    const error = result.error || result.NSLocalizedDescription;
    if (!result.ok) await callBTT("display_notification", {title: "Finder paste stopped", "": error});
    return result.ok ? result.result : error;
}'''
        [definition] = finder_paste.definitions()
        script = definition['BTTAdditionalActionData']['BTTAppleScriptString']
        self.assertEqual(script.replace(str(finder_paste.DIRECTORY), 'ROOT'), expected)

    def test_the_runtime_files_exist(self):
        for path in finder_paste.RUNTIME:
            self.assertTrue(Path(path).is_file(), path)


@unittest.skipUnless(os.environ.get('BTT_LIVE_TESTS') == '1', 'set BTT_LIVE_TESTS=1 to run the runtime inside BTT')
class LiveBTTTests(unittest.TestCase):
    def test_the_trigger_action_saves_a_named_pasteboard_image_inside_btt(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder)
        (folder / 'source.bin').write_bytes(png_fixture())
        name = 'finder-paste-test-%d' % os.getpid()
        board = '$.NSPasteboard.pasteboardWithName(%s)' % json.dumps(name)
        fill = ('ObjC.import("AppKit"); const board = %s; board.clearContents; '
                'board.setDataForType($.NSData.dataWithContentsOfFile(%s), "public.png");'
                % (board, json.dumps(str(folder / 'source.bin'))))
        subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', fill], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ['/usr/bin/osascript', '-l', 'JavaScript', '-e',
                                         'ObjC.import("AppKit"); %s.releaseGlobally;' % board], capture_output=True)
        action = {'BTTPredefinedActionType': 281,
                  'BTTAdditionalActionData': finder_paste.action({'pasteboard': name, 'destination': str(folder)})}
        output = subprocess.run([BTTCLI, 'trigger_action', 'json=' + json.dumps(action)],
                                capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        saved = [path for path in folder.iterdir() if path.name != 'source.bin']
        self.assertEqual([output], ['saved ' + path.name for path in saved])
        self.assertEqual(saved[0].read_bytes(), (folder / 'source.bin').read_bytes())


if __name__ == '__main__':
    unittest.main()
