"""Lazy CoreGraphics display adapter."""
from .model import DisplayInfo, MIRROR_NAME


def online_displays() -> list[DisplayInfo]:
    import Quartz
    from AppKit import NSScreen

    error, display_ids, _count = Quartz.CGGetOnlineDisplayList(16, None, None)
    if error != 0:
        raise RuntimeError(f"CGGetOnlineDisplayList failed with error {error}")
    names = {
        int(screen.deviceDescription()["NSScreenNumber"]): str(screen.localizedName())
        for screen in NSScreen.screens()
    }
    return [
        DisplayInfo(
            int(display_id),
            names.get(int(display_id))
            or (MIRROR_NAME if Quartz.CGDisplayIsInMirrorSet(display_id) else "unnamed"),
            bool(Quartz.CGDisplayIsBuiltin(display_id)),
            int(Quartz.CGDisplayVendorNumber(display_id)),
            int(Quartz.CGDisplayModelNumber(display_id)),
            int(Quartz.CGDisplayUnitNumber(display_id)),
            int(Quartz.CGDisplaySerialNumber(display_id)),
        )
        for display_id in display_ids
    ]
