"""The media-scrub installer: plan, transaction, rollback and wiring against an in-memory BTT."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import install_media_scrub as cli
import media_scrub_install as install
from macbook.bettertouchtool.btt_common import MEDIA
from macbook.tests.media_scrub_fake_btt import OLD_SCRIPT, FakeMediaBTT, media_fixture

PAGE = "<!doctype html><title>page</title>"
SOURCE = "// new script running media_scrub.py"
SCRUB, PPAUSE = install.SCRUB, install.PPAUSE


def plan_for(fake, page=PAGE, source=SOURCE):
    return install.make_plan(install.read_trigger(fake, MEDIA), install.read_trigger(fake, SCRUB), source, page)


def run_apply(fake, plan, saved=lambda: [], backups=None):
    events = backups if backups is not None else []
    def backup(snapshot):
        events.append(("backup", len(fake.commands("update_menu_item", "update_trigger", "add_new_trigger"))))
        return Path("/private/backup.json")
    return install.apply_plan(fake, plan, backup=backup, saved_check=saved, wait=lambda check: _wait(check))


def _wait(check):
    errors = check()
    if errors:
        raise RuntimeError("BTT has not saved the slider: " + "; ".join(errors))


def snapshot(fake):
    return install.canon(fake.media)


class PlanTests(unittest.TestCase):
    def test_new_item_goes_after_ppause_and_later_siblings_shift_by_one(self):
        plan = plan_for(FakeMediaBTT())
        self.assertEqual(plan["order"], 3)
        self.assertEqual(plan["final_orders"], {"prev": 0, "rew": 1, PPAUSE: 2, SCRUB: 3, "fwd": 4, "row": 5, "tools": 6})
        shifts = [(e["uuid"], e["old"], e["new"]) for e in plan["edits"] if e["scope"] == "order"]
        self.assertEqual(shifts, [("fwd", 3, 4), ("row", 4, 5), ("tools", 5, 6)])
        self.assertEqual(plan["touched"], sorted([PPAUSE, SCRUB, "fwd", "row", "tools"]))
        text = "\n".join(install.describe(plan))
        self.assertIn("order fwd: Fwd 3 -> 4", text)
        self.assertIn("BTTMenuScriptUpdateInterval -> 1", text)

    def test_ppause_edits_keep_other_script_settings(self):
        plan = plan_for(FakeMediaBTT())
        self.assertEqual(plan["settings"], {"BTTScriptType": 3, "BTTAppleScriptString": SOURCE,
                                            "BTTScriptFunctionToCall": "itemScript", "BTTScriptLocation": 0})

    def refuses(self, change, scrub=None):
        fake = FakeMediaBTT()
        change(fake.media)
        with self.assertRaises(install.Refusal):
            install.make_plan(fake.media, scrub, SOURCE, PAGE)

    def test_refusals_leave_nothing_written(self):
        ppause = lambda media: media["BTTMenuItems"][2]
        self.refuses(lambda m: m["BTTMenuItems"].remove(ppause(m)))
        self.refuses(lambda m: ppause(m)["BTTMenuConfig"]["BTTMenuScriptSettings"].update(BTTAppleScriptString="return 1"))
        self.refuses(lambda m: ppause(m)["BTTMenuConfig"].update(BTTMenuItemScriptActive=0))
        self.refuses(lambda m: ppause(m).update(BTTTriggerType=774))
        self.refuses(lambda m: ppause(m).update(BTTEnabled=0))
        self.refuses(lambda m: m["BTTMenuItems"][3].update(BTTOrder=3.5))
        self.refuses(lambda m: m["BTTMenuItems"][3].pop("BTTOrder"))
        self.refuses(lambda m: m.update(BTTTriggerType=774))
        self.refuses(lambda m: m["BTTMenuConfig"].update(BTTMenuElementIdentifier="Other"))
        self.refuses(lambda m: None, scrub={"BTTUUID": SCRUB, "BTTTriggerParentUUID": "elsewhere"})
        self.refuses(lambda m: m["BTTMenuItems"].append(dict(m["BTTMenuItems"][0])))

    def test_either_known_script_is_an_acceptable_prior(self):
        fake = FakeMediaBTT()
        fake.media["BTTMenuItems"][2]["BTTMenuConfig"]["BTTMenuScriptSettings"]["BTTAppleScriptString"] = \
            "'/x/media_scrub.py' status"
        self.assertTrue(plan_for(fake)["edits"])

    def test_render_play_status_substitutes_a_json_quoted_path(self):
        template = (cli.DIRECTORY / "play_status.js").read_text()
        rendered = install.render_play_status(template, '/a b/it\'s "x"/media_scrub.py')
        self.assertIn('const cli = "/a b/it\'s \\"x\\"/media_scrub.py";', rendered)
        self.assertNotIn("__MEDIA_SCRUB_PATH__", rendered)
        for bad in ("relative", "/bad\npath"):
            with self.assertRaises(ValueError):
                install.render_play_status(template, bad)


class ApplyTests(unittest.TestCase):
    def check_installed(self, fake, plan):
        self.assertEqual(fake.orders(), plan["final_orders"])
        item = fake.find(SCRUB)
        self.assertEqual(item["BTTTriggerType"], 778)
        config = item["BTTMenuConfig"]
        self.assertEqual((config["BTTMenuItemWebViewPlain"], config["BTTMenuItemKeepActiveInBackground"]), (0, 0))
        self.assertEqual((config["BTTMenuItemMinWidth"], config["BTTMenuItemMaxHeight"]), (220, 40))
        self.assertEqual(config["BTTMenuItemText"], PAGE)
        ppause = fake.find(PPAUSE)["BTTMenuConfig"]
        self.assertEqual(ppause["BTTMenuScriptUpdateInterval"], 1)
        self.assertEqual(ppause["BTTMenuScriptSettings"]["BTTAppleScriptString"], SOURCE)
        self.assertEqual(fake.find(PPAUSE)["BTTMenuItemActions"][0]["BTTUUID"], "play-action")

    def test_initial_install_converges_whether_or_not_btt_renumbers(self):
        for renumber in (False, True):
            for honor in (True, False):
                with self.subTest(renumber=renumber, honor_order=honor):
                    fake = FakeMediaBTT(renumber=renumber, honor_order=honor)
                    plan, events = plan_for(fake), []
                    message = run_apply(fake, plan, backups=events)
                    self.assertIn("Verified", message)
                    self.check_installed(fake, plan)
                    self.assertEqual(events, [("backup", 0)], "backup precedes every write")
                    self.assertTrue(install.is_noop(plan_for(fake)), "a rerun finds nothing to do")

    def test_rerun_updates_the_page_in_place_without_delete_or_add(self):
        fake = FakeMediaBTT()
        run_apply(fake, plan_for(fake))
        fake.log.clear()
        orders = fake.orders()
        plan = plan_for(fake, page="<!doctype html><title>v2</title>")
        self.assertTrue(plan["exists"])
        run_apply(fake, plan)
        self.assertEqual(fake.find(SCRUB)["BTTMenuConfig"]["BTTMenuItemText"], "<!doctype html><title>v2</title>")
        self.assertEqual(fake.commands("add_new_trigger", "delete_trigger", "update_trigger"), [])
        self.assertEqual(fake.orders(), orders)

    def test_hand_reordered_item_keeps_its_position(self):
        fake = FakeMediaBTT()
        run_apply(fake, plan_for(fake))
        fake.find(SCRUB)["BTTOrder"] = 5
        fake.find("row")["BTTOrder"] = 3
        plan = plan_for(fake, page="v2")
        self.assertEqual(plan["order"], 5)
        run_apply(fake, plan)
        self.assertEqual(fake.find(SCRUB)["BTTOrder"], 5)

    def test_failure_after_ppause_write_restores_everything(self):
        for renumber in (False, True):
            with self.subTest(renumber=renumber):
                fake = FakeMediaBTT(renumber=renumber)
                original = snapshot(fake)
                plan = plan_for(fake)
                def boom(command, parameters):
                    if command == "add_new_trigger":
                        raise RuntimeError("BTT refused the add")
                fake.before = boom
                with self.assertRaisesRegex(install.InstallFailed, "BTT refused the add") as caught:
                    run_apply(fake, plan)
                self.assertFalse(caught.exception.incomplete)
                self.assertEqual(snapshot(fake), original)
                self.assertEqual(fake.find(PPAUSE)["BTTMenuConfig"]["BTTMenuScriptSettings"]["BTTAppleScriptString"], OLD_SCRIPT)

    def test_failure_after_the_item_exists_deletes_it_and_unshifts_siblings(self):
        for renumber in (False, True):
            with self.subTest(renumber=renumber):
                fake = FakeMediaBTT(renumber=renumber)
                original = snapshot(fake)
                with self.assertRaises(install.InstallFailed) as caught:
                    run_apply(fake, plan_for(fake), saved=lambda: ["not saved yet"])
                self.assertFalse(caught.exception.incomplete)
                self.assertEqual(snapshot(fake), original)
                self.assertEqual(len(fake.commands("delete_trigger")), 1)

    def test_readback_mismatch_rolls_back(self):
        fake = FakeMediaBTT()
        original = snapshot(fake)
        def drop_interval(command, parameters):
            if command == "add_new_trigger":
                fake.find(PPAUSE)["BTTMenuConfig"]["BTTMenuScriptUpdateInterval"] = 2  # BTT reverted it
        fake.before = drop_interval
        with self.assertRaisesRegex(install.InstallFailed, "Readback failed.*PPause update interval"):
            run_apply(fake, plan_for(fake))
        self.assertEqual(snapshot(fake), original)

    def test_update_failure_restores_only_this_attempts_fields(self):
        fake = FakeMediaBTT()
        run_apply(fake, plan_for(fake))
        before = snapshot(fake)
        plan = plan_for(fake, page="v2")
        with self.assertRaises(install.InstallFailed) as caught:
            run_apply(fake, plan, saved=lambda: ["pending"])
        self.assertFalse(caught.exception.incomplete)
        self.assertEqual(snapshot(fake), before)
        self.assertIsNotNone(fake.find(SCRUB), "an item this attempt did not create is never deleted")
        self.assertEqual(fake.commands("delete_trigger"), [])

    def test_concurrent_edit_is_preserved_and_reported(self):
        fake = FakeMediaBTT()
        plan = plan_for(fake)
        def someone_edits(command, parameters):
            if command == "add_new_trigger":
                fake.find(PPAUSE)["BTTMenuConfig"]["BTTMenuScriptUpdateInterval"] = 5
                fake.find("tools")["BTTMenuConfig"]["BTTMenuElementIdentifier"] = "Renamed"
                raise RuntimeError("add failed")
        fake.before = someone_edits
        with self.assertRaises(install.InstallFailed) as caught:
            run_apply(fake, plan)
        self.assertTrue(caught.exception.incomplete)
        self.assertIn("ROLLBACK INCOMPLETE", str(caught.exception))
        self.assertIn("changed by someone else", str(caught.exception))
        self.assertIn("/private/backup.json", str(caught.exception))
        ppause = fake.find(PPAUSE)["BTTMenuConfig"]
        self.assertEqual(ppause["BTTMenuScriptUpdateInterval"], 5, "the concurrent value stays")
        self.assertEqual(ppause["BTTMenuScriptSettings"]["BTTAppleScriptString"], OLD_SCRIPT, "ours is restored")
        self.assertEqual(fake.find("tools")["BTTMenuConfig"]["BTTMenuElementIdentifier"], "Renamed")

    def test_a_sibling_moved_by_someone_else_is_not_overwritten(self):
        fake = FakeMediaBTT()
        def move(command, parameters):
            if command == "add_new_trigger":
                fake.find("row")["BTTOrder"] = 9
        fake.before = move
        with self.assertRaises(install.InstallFailed) as caught:
            run_apply(fake, plan_for(fake), saved=lambda: ["x"])
        self.assertTrue(caught.exception.incomplete)
        self.assertEqual(fake.find("row")["BTTOrder"], 9)

    def test_failed_delete_is_reported_as_incomplete(self):
        fake = FakeMediaBTT()
        def refuse_delete(command, parameters):
            if command == "delete_trigger":
                raise RuntimeError("delete refused")
        fake.before = refuse_delete
        with self.assertRaises(install.InstallFailed) as caught:
            run_apply(fake, plan_for(fake), saved=lambda: ["x"])
        self.assertTrue(caught.exception.incomplete)
        self.assertIn("delete refused", str(caught.exception))

    def test_media_changed_before_apply_or_during_backup_writes_nothing(self):
        fake = FakeMediaBTT()
        plan = plan_for(fake)
        fake.find("fwd")["BTTMenuName"] = "Hand edit"
        with self.assertRaises(install.Refusal):
            run_apply(fake, plan)
        self.assertEqual(fake.commands("update_menu_item", "update_trigger", "add_new_trigger"), [])
        fake = FakeMediaBTT()
        plan = plan_for(fake)
        def backup(snapshot):
            fake.find("fwd")["BTTMenuName"] = "Edited during backup"
            return Path("/b")
        with self.assertRaisesRegex(install.Refusal, "during backup"):
            install.apply_plan(fake, plan, backup=backup, saved_check=lambda: [])
        self.assertEqual(fake.commands("update_menu_item", "update_trigger", "add_new_trigger"), [])

    def test_unexpected_sibling_appearing_mid_install_rolls_back(self):
        fake = FakeMediaBTT()
        original = snapshot(fake)
        def intruder(command, parameters):
            if command == "add_new_trigger":
                fake.media["BTTMenuItems"].append({"BTTUUID": "intruder", "BTTOrder": 40, "BTTTriggerType": 773})
        fake.before = intruder
        with self.assertRaisesRegex(install.InstallFailed, "unexpected item intruder"):
            run_apply(fake, plan_for(fake))
        fake.media["BTTMenuItems"] = [c for c in fake.media["BTTMenuItems"] if c["BTTUUID"] != "intruder"]
        self.assertEqual(snapshot(fake), original)


class DisabledSiblingTests(unittest.TestCase):
    def disabled_fake(self, **kwargs):
        fake = FakeMediaBTT(**kwargs)
        fake.find("fwd")["BTTEnabled"] = 0
        fake.find("fwd")["BTTEnabled2"] = 0
        return fake

    def test_install_keeps_a_disabled_sibling_disabled(self):
        for renumber in (False, True):
            with self.subTest(renumber=renumber):
                fake = self.disabled_fake(renumber=renumber)
                run_apply(fake, plan_for(fake))
                self.assertEqual((fake.find("fwd")["BTTEnabled"], fake.find("fwd")["BTTEnabled2"]), (0, 0))
                self.assertEqual(fake.find("fwd")["BTTOrder"], 4)

    def test_rollback_restores_order_and_leaves_the_sibling_disabled(self):
        for renumber in (False, True):
            with self.subTest(renumber=renumber):
                fake = self.disabled_fake(renumber=renumber)
                original = snapshot(fake)
                with self.assertRaises(install.InstallFailed) as caught:
                    run_apply(fake, plan_for(fake), saved=lambda: ["pending"])
                self.assertFalse(caught.exception.incomplete)
                self.assertEqual(snapshot(fake), original)
                self.assertEqual(fake.find("fwd")["BTTEnabled"], 0)

    def test_every_order_patch_carries_the_exported_flags(self):
        fake = self.disabled_fake()
        with self.assertRaises(install.InstallFailed):
            run_apply(fake, plan_for(fake), saved=lambda: ["pending"])
        patches = [json.loads(p["json"]) for c, p in fake.log if c == "update_trigger" and p["uuid"] == "fwd"]
        self.assertTrue(patches)
        self.assertTrue(all(p["BTTEnabled"] == 0 and p["BTTEnabled2"] == 0 for p in patches))


class SavedStoreTests(unittest.TestCase):
    def saved(self, plan, **overrides):
        records = {uid: {"parent": MEDIA, "ZORDER": order, "ZGESTURETYPE": 773, "ZENABLEDNEW": 1, "config": {}}
                   for uid, order in plan["final_orders"].items()}
        records[SCRUB].update(ZGESTURETYPE=778, config=install.scrub_config(PAGE))
        records[PPAUSE]["config"] = {"BTTMenuScriptUpdateInterval": 1, "BTTMenuScriptSettings": plan["settings"]}
        for uid, change in overrides.items():
            records[uid].update(change)
        return records

    def test_saved_records_are_checked_for_order_parent_config_and_ppause(self):
        plan = plan_for(FakeMediaBTT())
        self.assertEqual(install.saved_errors(self.saved(plan), plan), [])
        for uid, change in {"fwd": {"ZORDER": 3}, "row": {"parent": "other"}, SCRUB: {"ZGESTURETYPE": 773},
                            SCRUB: {"ZENABLEDNEW": 0}, PPAUSE: {"config": {"BTTMenuScriptUpdateInterval": 2}}}.items():
            with self.subTest(uid=uid):
                self.assertTrue(install.saved_errors(self.saved(plan, **{uid: change}), plan))
        missing = self.saved(plan)
        del missing[SCRUB]
        self.assertTrue(install.saved_errors(missing, plan))

    def test_wait_saved_polls_until_clean_then_times_out(self):
        results = [["a"], ["a"], []]
        install.wait_saved(lambda: results.pop(0), sleep=lambda s: None)
        self.assertEqual(results, [])
        clock = iter(range(0, 1000, 20))
        with self.assertRaisesRegex(RuntimeError, "not saved the slider: a"):
            install.wait_saved(lambda: ["a"], sleep=lambda s: None, monotonic=lambda: next(clock))


class MainWiringTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def run_main(self, argv, fake, *, saved_ok=True, script_exists=True):
        events = []
        with ExitStack() as stack:
            root = Path(self.directory.name)
            script = root / "media_scrub.py"
            if script_exists:
                script.touch()
            stack.enter_context(patch.object(cli, "SCRIPT", script))
            stack.enter_context(patch.object(cli, "BACKUPS", root / "backups"))
            stack.enter_context(patch.object(cli.btt_socket, "call", fake))
            stack.enter_context(patch.object(cli, "current_database", return_value=root / "db"))
            stack.enter_context(patch.object(cli, "records", side_effect=lambda db: events.append("saved") or {}))
            stack.enter_context(patch.object(install, "wait_saved", side_effect=lambda check: (
                events.append("wait"), None if saved_ok else (_ for _ in ()).throw(RuntimeError("not saved")))))
            stack.enter_context(patch.object(cli, "backup_configuration",
                                             side_effect=lambda *a, **k: events.append("backup") or root / "b.json"))
            before = stack.enter_context(patch.object(cli, "before_install",
                                                      side_effect=lambda **kw: events.append("baseline") or {"fixture": 1}))
            after = stack.enter_context(patch.object(cli, "after_install",
                                                     side_effect=lambda *a, **kw: events.append("checkpoint")))
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    status = cli.main(argv)
                except (install.Refusal, install.InstallFailed) as error:
                    status = 1
                    err.write(str(error))
        return status, events, before, after, out.getvalue(), err.getvalue()

    def test_inspect_prints_the_plan_and_writes_nothing(self):
        fake = FakeMediaBTT()
        status, events, before, after, out, _ = self.run_main([], fake)
        self.assertEqual(status, 0)
        self.assertEqual(events, [])
        self.assertEqual(fake.commands("update_menu_item", "update_trigger", "add_new_trigger", "delete_trigger"), [])
        before.assert_not_called()
        after.assert_not_called()
        self.assertIn("after PPause at order 3", out)
        self.assertIn("order fwd: Fwd 3 -> 4", out)

    def test_apply_checkpoints_only_after_saved_verification_with_every_written_uuid(self):
        fake = FakeMediaBTT()
        status, events, before, after, out, _ = self.run_main(["--apply"], fake)
        self.assertEqual(status, 0, out)
        self.assertEqual(events, ["baseline", "backup", "wait", "checkpoint"])
        before.assert_called_once_with(no_checkpoint=False)
        args, kwargs = after.call_args
        self.assertEqual(sorted(args[1]), sorted([PPAUSE, SCRUB, "fwd", "row", "tools"]))
        self.assertEqual((args[2], kwargs), ("install_media_scrub", {"wrote": True}))

    def test_no_checkpoint_flag_and_noop_rerun(self):
        fake = FakeMediaBTT()
        _, _, before, _, _, _ = self.run_main(["--apply", "--no-checkpoint"], fake)
        before.assert_called_once_with(no_checkpoint=True)
        # The fake now holds the installed item; the rerun's page differs only if the script path does.
        status, events, _, after, out, _ = self.run_main(["--apply"], fake)
        self.assertEqual(status, 0)
        self.assertIn("Already configured", out)
        self.assertFalse(after.call_args.kwargs["wrote"])
        self.assertNotIn("backup", events)

    def test_failed_saved_verification_never_checkpoints_and_restores(self):
        fake = FakeMediaBTT()
        original = snapshot(fake)
        status, events, _, after, _, err = self.run_main(["--apply"], fake, saved_ok=False)
        self.assertEqual(status, 1)
        after.assert_not_called()
        self.assertEqual(snapshot(fake), original)
        self.assertIn("Every changed field was restored", err)

    def test_apply_refuses_without_the_cli_script(self):
        fake = FakeMediaBTT()
        status, events, before, _, out, _ = self.run_main(["--apply"], fake, script_exists=False)
        self.assertEqual(status, 1)
        self.assertEqual(fake.commands("update_menu_item", "add_new_trigger"), [])
        before.assert_not_called()

    def test_plan_bakes_the_absolute_script_path_into_page_and_ppause(self):
        fake = FakeMediaBTT()
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "has space" / "media_scrub.py"
            plan = cli.build_plan(fake, script)
        self.assertIn(json.dumps(str(script)), plan["play_source"])
        self.assertIn(json.dumps({"cli_path": str(script)}), plan["page"])


if __name__ == "__main__":
    unittest.main()
