#!/bin/zsh
set -eu -o pipefail

source_dir=${0:A:h}
# A user-owned Applications bundle gives TCC a stable, Finder-visible identity.
applications="$HOME/Applications"
destination="$applications/MeteredWiFi.app"
/bin/mkdir -p "$applications"
work=$(/usr/bin/mktemp -d "$applications/.metered-wifi-build.XXXXXX")
trap '/bin/rm -rf -- "$work"' EXIT
bundle="$work/MeteredWiFi.app"
/bin/mkdir -p "$bundle/Contents/MacOS"
/bin/cp "$source_dir/Info.plist" "$bundle/Contents/Info.plist"
/usr/bin/plutil -lint "$bundle/Contents/Info.plist"
/usr/bin/xcrun swiftc -swift-version 6 -O -target arm64-apple-macosx14.0 \
    -module-cache-path "$work/modules" \
    -framework AppKit -framework CoreLocation -framework CoreWLAN \
    "$source_dir/main.swift" "$source_dir/WiFiProbe.swift" \
    -o "$bundle/Contents/MacOS/MeteredWiFi"
bundle_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$bundle/Contents/Info.plist")
# Ad-hoc defaults can pin the code hash. An explicit designated requirement is stable across builds.
/usr/bin/codesign --force --sign - --timestamp=none --identifier "$bundle_id" \
    --requirements "=designated => identifier \"$bundle_id\"" "$bundle"
/usr/bin/codesign --verify --strict "$bundle"
if [[ -L "$destination" ]]; then
    print -u2 'Refusing to replace a symlinked app.'
    exit 1
fi
if [[ -e "$destination" ]]; then
    installed_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$destination/Contents/Info.plist")
    [[ "$installed_id" == "$bundle_id" ]] || { print -u2 'Refusing to replace a different app.'; exit 1; }
    /bin/mv "$destination" "$work/previous.app"
fi
if ! /bin/mv "$bundle" "$destination"; then
    if [[ -d "$work/previous.app" ]]; then
        # Keep the previous app if publishing the newly signed bundle failed.
        /bin/mv "$work/previous.app" "$destination" || {
            trap - EXIT
            print -u2 -r -- "Restore failed; previous app retained at $work/previous.app"
            exit 1
        }
    fi
    exit 1
fi
print -r -- "Installed $destination"
/usr/bin/codesign -d -r- "$destination" 2>&1
