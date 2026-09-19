# Strip image metadata

The native `Strip Metadata.app` opens a persistent drop window with a **Choose
Images…** button. Drop files repeatedly, or select multiple images at once. The
window reports each result and offers **Show Copies in Finder**.

`photo.jpg` becomes `photo_stripped.jpg` beside the original. Existing files are
never replaced; subsequent copies become `photo_stripped_2.jpg`, etc. Inputs are
never edited. Errors are reported per file, and remaining files continue.

## Build, install, run

The runtime needs only macOS. Building requires Apple's Command Line Tools
(`xcode-select --install` if missing).

```sh
cd ~/dev/scripts
/usr/bin/python3 macbook/scripts/build_strip_metadata_app.py
/usr/bin/python3 macbook/bettertouchtool/install_strip_metadata_menu.py --apply
open "macbook/build/Strip Metadata.app"
```

The installer adds a **Tools** submenu to the existing **Media** main floating
menu, containing a two-line **Strip / Metadata** item and a Back button. It uses
the live BTT AppleScript API, saves a local backup before making changes, and
checks the resulting item and shell action. Re-running it reuses the existing
Tools submenu and does not duplicate the item. Use `--parent-uuid UUID` for a
different parent floating menu. Without `--apply`, it only generates the JSON.

The agent sandbox cannot access BTT's AppleEvents service on this Mac, and BTT's
socket server is disabled. Run the installation command from normal Terminal.
There is no need to enable the socket server or restart BTT. BTT's database is
never edited directly. Menu layout and the app window need desktop verification.

If parent-menu validation fails, do not use `sudo`: this is a response validation
failure, not a filesystem permission error. Inspect only menu names/IDs/state with:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_strip_metadata_menu.py --inspect
```

The installer selects the exact parent UUID from the floating-menu list, accepts
numeric strings and BTT's omitted enabled default, and still refuses disabled,
missing, or ambiguous parents. API response regression tests run with
`node --test macbook/tests/test_strip_metadata_menu.cjs` after generating the JSON.

The shell entry point is also a CLI:

```sh
/bin/zsh ~/dev/scripts/macbook/scripts/strip-image-metadata.zsh
/bin/zsh ~/dev/scripts/macbook/scripts/strip-image-metadata.zsh "/path/to/photo.jpg"
```

## Image behavior and limitations

The app decodes every frame, applies orientation, renders fresh 8-bit sRGB pixels,
and writes a new file. It does not copy source EXIF, GPS, IPTC, XMP, maker notes,
comments, camera/owner identifiers, capture dates, embedded thumbnails, custom
color profiles, Finder tags, or extended attributes. Standard encoder-generated
technical fields (dimensions, color space, etc.) can remain. Animation timing and
loop counts are explicitly copied for GIF/APNG; no other source metadata is copied.

- This is a rendered sharing copy, **not lossless metadata surgery**. JPEG/HEIC
  are re-encoded at maximum quality; wide-gamut/HDR/high-bit-depth images become
  8-bit sRGB. RAW development, layers and auxiliary depth/gain-map data are not
  retained. Keep originals for editing.
- Formats macOS ImageIO can both read and write keep their format and extension.
  Read-only formats such as WebP and supported camera RAW become `_stripped.png`.
  Unsupported images (including SVG in the tested environment) report an error.
  Unsupported multi-frame formats are refused instead of discarding frames.
- Each frame is limited to 150 megapixels. Writing must be allowed in the source
  folder. Filesystem support for atomic exclusive rename is required.
- The filename and visible image content remain, so identifying information in
  either is not removed. New filesystem creation/modification dates are normal.

The implementation uses Apple's
[ImageIO orientation transform](https://developer.apple.com/documentation/imageio/kcgimagesourcecreatethumbnailwithtransform)
and BTT's [AppleScript API](https://docs.folivora.ai/docs/scripting/apple-script/).
The installer follows the scripting dictionary in the installed BTT app.

## Validation

```sh
cd ~/dev/scripts
/usr/bin/python3 -m unittest discover -s macbook/tests -p test_strip_image_metadata.py -v
```

Tests use ExifTool to plant and independently inspect metadata; ExifTool is not a
runtime dependency. Integration coverage includes JPG/PNG/TIFF/GIF/BMP metadata,
source hashes, extended attributes, orientation, PNG pixels/transparency, animated
GIF frame count/duration, WebP fallback, special filenames, concurrent output
collisions, invalid inputs, and batch error handling.

HEIC has a conditional integration test. This sandbox blocks the macOS HEIC
encoder, so HEIC must be validated by rerunning tests from normal Terminal.
Neither real camera RAW nor complex optimized animations have been validated.
