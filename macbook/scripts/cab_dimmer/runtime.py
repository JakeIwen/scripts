"""CoreGraphics callback and polling runtime for the display dimmer."""
import os
import time

import tool_settings

from .model import MONITOR_NAME, PlugTracker

TOOL = "display-cab-dimmer"
FALLBACK_POLL_SECONDS = 20.0
RUN_LOOP_SECONDS = 1.0
BEGIN_CONFIGURATION_FLAG = 1


def _reload_settings(reloader, settings, ignored_error, logger):
    result = reloader.poll()
    if result.error is not None:
        if result.error != ignored_error:
            logger(f"settings ignored, keeping {settings}: {result.error}")
        return settings, result.error
    if result.changed:
        enabled = "on" if result.values["enabled"] else "off"
        logger(
            f"settings reloaded; enabled {enabled}; brightness {result.values['brightness']}%; "
            f"settle_seconds {result.values['settle_seconds']:g}"
        )
    return result.values, None


def watch(*, online_displays, matching_snapshot, matching_monitor_names,
          update_state, handle_plug_in, logger):
    import Quartz
    from AppKit import NSWorkspace
    from Foundation import NSObject

    reloader = tool_settings.Reloader(TOOL)
    settings = reloader.poll().values
    initial_displays = online_displays()
    initial_snapshot = matching_snapshot(initial_displays)
    monitors = matching_monitor_names(initial_displays)
    tracker = PlugTracker(initial_snapshot)
    runtime = {"settle_seconds": float(settings["settle_seconds"])}
    started_at = time.time()
    update_state({"agent": {"pid": os.getpid(), "started_at": started_at},
                  "monitors": monitors, "monitors_at": started_at})

    def reconfigured(_display, flags, _info):
        if int(flags) & BEGIN_CONFIGURATION_FLAG:
            return
        tracker.schedule(time.monotonic(), runtime["settle_seconds"])

    class WorkspaceObserver(NSObject):
        def sleep_(self, _notification):
            tracker.will_sleep()

        def wake_(self, _notification):
            tracker.did_wake(time.monotonic(), runtime["settle_seconds"])

    observer = WorkspaceObserver.alloc().init()
    notifications = NSWorkspace.sharedWorkspace().notificationCenter()
    notifications.addObserver_selector_name_object_(
        observer, "sleep:", "NSWorkspaceWillSleepNotification", None)
    notifications.addObserver_selector_name_object_(
        observer, "wake:", "NSWorkspaceDidWakeNotification", None)
    callback_error = Quartz.CGDisplayRegisterReconfigurationCallback(reconfigured, None)
    if callback_error != 0:
        notifications.removeObserver_(observer)
        raise RuntimeError(f"display callback registration failed with error {callback_error}")

    ignored_error = None
    next_poll = time.monotonic() + FALLBACK_POLL_SECONDS
    logger(f"watching for {MONITOR_NAME}; baseline has {len(initial_snapshot)} matching display(s)")
    try:
        while True:
            Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, RUN_LOOP_SECONDS, True)
            settings, ignored_error = _reload_settings(reloader, settings, ignored_error, logger)
            runtime["settle_seconds"] = float(settings["settle_seconds"])
            now = time.monotonic()
            fallback = now >= next_poll
            if not tracker.due(now) and not fallback:
                continue
            if fallback:
                next_poll = now + FALLBACK_POLL_SECONDS
            if tracker.asleep:
                continue
            try:
                displays = online_displays()
                snapshot = matching_snapshot(displays)
                current_monitors = matching_monitor_names(displays)
            except Exception as error:
                tracker.abandon_pending_check()
                logger(f"display check skipped: {type(error).__name__}: {error}")
                continue
            plugged_in = tracker.evaluate(snapshot, now, fallback=fallback)
            if current_monitors != monitors:
                monitors = current_monitors
                update_state({"monitors": monitors, "monitors_at": time.time()})
            if plugged_in:
                handle_plug_in(settings)
    finally:
        Quartz.CGDisplayRemoveReconfigurationCallback(reconfigured, None)
        notifications.removeObserver_(observer)
