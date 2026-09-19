"""Exercise real SQLite selection and generated note-ID actions without Notes UI."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import shlex
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/notes_menu.py"
SPEC = importlib.util.spec_from_file_location("notes_menu", SOURCE)
notes_menu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(notes_menu)


class NotesMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "notes.sqlite"
        with sqlite3.connect(self.database) as db:
            db.executescript("""
                CREATE TABLE Z_METADATA (Z_UUID TEXT);
                INSERT INTO Z_METADATA VALUES ('56EA8F9B-7504-4D57-8228-75F165DAA610');
                CREATE TABLE Z_PRIMARYKEY (Z_ENT INTEGER, Z_NAME TEXT);
                INSERT INTO Z_PRIMARYKEY VALUES (12, 'ICNote');
                CREATE TABLE ZICCLOUDSYNCINGOBJECT (
                    Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, ZTITLE1 TEXT,
                    ZTITLE2 TEXT, ZFOLDER INTEGER, ZFOLDERTYPE INTEGER,
                    ZISPINNED INTEGER, ZMARKEDFORDELETION INTEGER,
                    ZMODIFICATIONDATE1 REAL);
                INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK,Z_ENT,ZTITLE2,ZFOLDERTYPE)
                    VALUES (100,15,'Notes',0),(101,15,'Trash',1);
                INSERT INTO ZICCLOUDSYNCINGOBJECT
                    (Z_PK,Z_ENT,ZTITLE1,ZFOLDER,ZISPINNED,ZMARKEDFORDELETION,ZMODIFICATIONDATE1)
                    VALUES (1,12,'Older pin',100,1,0,10),
                           (2,12,'Newest',100,0,0,40),
                           (3,12,'Newer pin',100,1,0,30),
                           (4,12,'Deleted',100,1,1,50),
                           (5,12,'Trashed',101,1,0,60),
                           (6,12,'Orphan',NULL,1,0,70),
                           (7,12,'',100,1,0,80);
            """)

    def test_pins_recent_sort_and_exclusions(self):
        before = self.database.read_bytes()
        pinned = notes_menu.read_notes(self.database, "pinned")
        self.assertEqual([x["title"] for x in pinned], ["Newer pin", "Older pin"])
        recent = notes_menu.read_notes(self.database, "recent", 2)
        self.assertEqual([x["title"] for x in recent], ["Newest", "Newer pin"])
        self.assertEqual(pinned[0]["id"],
                         "x-coredata://56EA8F9B-7504-4D57-8228-75F165DAA610/ICNote/p3")
        self.assertEqual(self.database.read_bytes(), before)

    def test_titles_are_literal_and_not_shell_code(self):
        title = 'Work::size@@999; "$(touch nope)"\n🎸'
        entries = [{"title": title, "folder": "Notes", "id": "x-coredata://store/ICNote/p1"},
                   {"title": title, "folder": "Music", "id": "x-coredata://store/ICNote/p2"}]
        opener = Path("/tmp/it's a folder/show_note.sh")
        items = notes_menu.menu_items(entries, "pinned", opener)
        self.assertEqual(items[0]["title"]["text"], title.replace('\n', ' ') + " — Notes")
        self.assertEqual(items[1]["title"]["text"], title.replace('\n', ' ') + " — Music")
        for index, item in enumerate(items):
            js = item["action"]["js"]
            command = json.loads(js.removeprefix('runShellScript({script: ').removesuffix('})'))
            self.assertEqual(shlex.split(command), ["/bin/bash", str(opener), "--id", entries[index]["id"]])
            self.assertNotIn('touch nope', command)

    def test_fail_closed_on_schema_change_and_missing_database(self):
        missing = Path(self.temp.name) / "absent.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            notes_menu.read_notes(missing, "pinned")
        self.assertFalse(missing.exists())
        with sqlite3.connect(self.database) as db:
            db.execute('ALTER TABLE ZICCLOUDSYNCINGOBJECT RENAME COLUMN ZISPINNED TO CHANGED')
        with self.assertRaisesRegex(ValueError, 'format changed'):
            notes_menu.read_notes(self.database, "pinned")

    def test_empty_list_is_explicit_and_has_no_action(self):
        item = notes_menu.menu_items([], "pinned")[0]
        self.assertEqual(item["title"]["text"], 'No pinned notes')
        self.assertNotIn('action', item)

    def assert_palette(self, item):
        self.assertEqual(item['title']['color'], '#EEEDE9')
        self.assertEqual(item['title']['size'], 16)
        self.assertEqual(item['background'], '#2B2D31')
        self.assertTrue(item['icon'].endswith('::color@@#EEEDE9'))
        self.assertEqual(item['BTTMenuItemShowHoverEffect'], 1)
        for suffix in ('', 'Dark'):
            self.assertEqual(item[f'BTTMenuItemBackgroundType{suffix}'], 4)
            self.assertEqual(item[f'BTTMenuItemBackgroundColor{suffix}'], '43, 45, 49, 255')
            self.assertEqual(item[f'BTTMenuItemBackgroundColorHover{suffix}'], '54, 57, 63, 255')
            self.assertEqual(item[f'BTTMenuItemFontColorHover{suffix}'], '238, 237, 233, 255')
            self.assertEqual(item[f'BTTMenuItemIconColor1Hover{suffix}'], '238, 237, 233, 255')
            self.assertEqual(item[f'BTTMenuItemBorderColorHover{suffix}'], '0, 0, 0, 0')
        self.assertNotIn('BTTMenuConfig', item)
        self.assertFalse(any(word in key for key in item for word in
                             ('Width', 'Height', 'Modifier', 'DisableDrag', 'CloseOn')))

    def test_both_menus_use_uniform_colors_without_extra_actions(self):
        for mode in ('recent', 'pinned'):
            notes = notes_menu.read_notes(self.database, mode)
            before = json.dumps(notes)
            for note, item in zip(notes, notes_menu.menu_items(notes, mode)):
                self.assert_palette(item)
                self.assertEqual(item['title']['text'], note['title'])
                self.assertEqual(list(item['action']), ['js'])
                self.assertNotIn('actions', item)  # No scripted hover/click hooks.
            self.assertEqual(json.dumps(notes), before)

    def test_empty_and_error_rows_share_the_palette(self):
        for mode in ('recent', 'pinned'):
            self.assert_palette(notes_menu.menu_items([], mode)[0])
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['notes_menu.py', 'recent', '--database',
                                 str(Path(self.temp.name) / 'missing.sqlite')]), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(notes_menu.main(), 0)
        error = json.loads(stdout.getvalue())[0]
        self.assert_palette(error)
        self.assertTrue(error['title']['text'].startswith('Notes unavailable'))
        self.assertNotIn('action', error)


if __name__ == '__main__':
    unittest.main()
