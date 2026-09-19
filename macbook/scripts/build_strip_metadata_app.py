#!/usr/bin/env python3
"""Build the native metadata stripper without installing system dependencies."""
from pathlib import Path
import plistlib
import subprocess

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "build" / "Strip Metadata.app"


def main():
    contents = APP / "Contents"
    executable = contents / "MacOS" / "StripMetadata"
    executable.parent.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "build" / "swift-module-cache"
    cache.mkdir(exist_ok=True)
    subprocess.run([
        "/usr/bin/xcrun", "swiftc", "-swift-version", "5", "-O",
        "-module-cache-path", str(cache),
        str(ROOT / "scripts" / "strip_image_metadata.swift"), "-o", str(executable),
    ], check=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "StripMetadata",
        "CFBundleIdentifier": "local.jacobr.strip-image-metadata",
        "CFBundleName": "Strip Metadata",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
    }))
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(APP)], check=True)
    print(APP)


if __name__ == "__main__":
    main()
