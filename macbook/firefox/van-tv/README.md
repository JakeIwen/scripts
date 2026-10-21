# Van TV

Private Firefox extension for the [Van TV receiver](../../../pi/projects/youtube-cast/README.md).
Cast the current YouTube video and playback position, cast a video link from
its beginning, or control the Pi from the toolbar popup. Requires the van LAN
and the receiver's direct `/api/cast` endpoint; no YouTube pairing, account
credentials, external libraries, or extension telemetry.

## Install in Firefox Developer Edition

1. In **Firefox Developer Edition**, open `about:config`, accept the warning,
   and set `xpinstall.signatures.required` to `false`. This allows unsigned
   extensions generally, so install only add-ons you trust. Release Firefox
   does not support this installation route. There is no supported Firefox CLI
   for this preference or the add-on installation dialogs.
2. Build the unsigned XPI from any working directory:

   ```sh
   zsh ~/dev/scripts/macbook/firefox/van-tv/build.zsh
   ```

   The builder uses the macOS `/usr/bin/zip`, needs no Node packages, and prints
   `~/dev/scripts/macbook/firefox/van-tv/dist/van-tv.xpi` as an absolute path.
3. Open `about:addons`, click the gear menu → **Install Add-on From File…**,
   select that XPI, and accept the listed permissions. Pin **Van TV** to the
   toolbar through Firefox's Extensions menu if desired. Reload already-open
   YouTube tabs after installing or updating.

Rebuild and install the XPI again after changing the source. Keep the stable
extension ID; bump `manifest.json`'s version when publishing a new private
version. The generated `dist/` directory is ignored by Git.

## Temporary development load

Open `about:debugging#/runtime/this-firefox`, choose **Load Temporary Add-on…**,
and select `~/dev/scripts/macbook/firefox/van-tv/manifest.json` (not the XPI).
This development install does not require disabling signature enforcement and
lasts until Firefox exits. After source edits, click **Reload** for Van TV,
then reload the YouTube tab. **Inspect** opens its background console.

## Controls

- On a YouTube watch page, the cast-shaped **Van TV** button sits in the
  player's right-hand controls. Hover for its label. Clicking sends the current
  playhead; after the receiver accepts the request, local playback pauses.
  Green feedback means accepted, not that asynchronous Pi playback has started.
  Errors appear in the button's hover title and accessible label.
- Right-click a YouTube video link → **Cast to Van TV**. A link's `t=` start
  time is honoured. On a watch page, right-click the page's
  background for the same menu item, casting the current tab's position.
- The toolbar popup shows receiver state, title and elapsed/total time, polls
  about every two seconds while open, and offers **Pause**, **Resume**, **Stop**
  and **Cast this tab**. The cast control works for watch, short-link, shorts,
  live and embed URLs. Where a content script is unavailable it uses the URL's
  start time (`t=`, `start=`, or `#t=`), and cannot pause that page itself.
- **Alt+Shift+V** (Option+Shift+V on the Mac) casts the active YouTube tab.
  Change it at `about:addons` → gear menu → **Manage Extension Shortcuts**.
  A failed shortcut/context-menu cast puts `!` on the toolbar button; hover for
  its error, or open the popup to check the receiver and clear the badge.

## Receiver and permissions

`config.js` defines addresses, YouTube URL patterns and shared command/state
names. It is a classic script shared with the isolated content script; the ES
modules import it for its configuration object. The background page alone
performs HTTP: primary `http://vanpi.lan:8795`, with the LAN IP
`http://192.168.6.103:8795` as the network-failure fallback. No Tailscale fallback.

- `GET /api/status` supplies state. Failed reads may try the other address.
- `POST /api/cast` sends `{ videoId, position }`.
- `POST /api/control` sends `{ action }` for pause, resume or stop.

Both POSTs use `Content-Type: application/json`. Before a write, a read selects
a reachable address; an ambiguous POST failure is **not retried** on the other
address, so a lost response cannot silently start the video twice. Requests
omit cookies, reject redirects and time out. The server must accept absent,
`null`, or `moz-extension://` origins for these POSTs. A 404 means the receiver
may still need the direct-cast API update.

The content script sends extension messages, never HTTP from YouTube, avoiding
mixed-content and page-origin CORS failures. No web-accessible extension
resources are declared. Receiver data is displayed as text, never HTML.

Manifest V2 permissions:

- `contextMenus` — video-link and watch-page casting menus.
- `tabs` — read the active tab's URL for popup, shortcut and URL-only fallback;
  it does not read or save browsing history.
- `http://vanpi.lan/*`, `http://192.168.6.103/*` — background access to the LAN
  receiver. Firefox match patterns cannot restrict the permission to port 8795;
  the implementation uses that port only.
- Content scripts match `*://www.youtube.com/*`, including its SPA navigation.
  No storage, cookies, downloads, scripting or notification permission is used.

The Pi controls are unauthenticated trusted-LAN endpoints. Never expose them
to the public internet. This extension does not replace the receiver's ad-free
playback implementation or make Firefox itself ad-free.

## Files and checks

- `youtube-video.mjs`: pure URL/video-ID/start-time parser.
- `receiver-client.mjs`: timeout, failover, response validation and HTTP boundary.
- `tab-casting.mjs`: tab selection, live playhead and safe pause acknowledgement.
- `background-actions.mjs`: message routes, menus and shortcut; `background.mjs`
  wires them to the background page.
- `content.js` / `content.css`: player button, SPA observer and page pause.
- `popup.mjs` / `popup-state.mjs`: popup messages, polling and state presentation.
- `tokens.css`: shared colors; `popup.css`: popup layout.
- `tests/*.test.mjs`: Node's built-in tests; no package install required.

From the repository root:

```sh
.local/test-venv/bin/python run_tests.py
```

The whole suite includes this extension's parser, local-HTTP client,
message/tab integration, state presentation and XPI packaging tests.

## Manual acceptance checks

No Firefox runtime was used to verify this extension during implementation.
Test in Developer Edition on the van LAN:

1. Install or temporarily load it. Reload an open YouTube watch tab. Confirm
   exactly one Van TV control, then navigate to another video, go Back/Forward,
   open a non-watch YouTube page, and return. The control should disappear away
   from watch pages, reappear once, and survive theater/fullscreen changes.
2. Play a video past 30 seconds and click Van TV. Confirm the Pi loads the same
   video near that playhead, Firefox pauses only after acceptance, and the
   button briefly shows success. Check the Pi picture and rear Sonos audio.
3. Right-click a different `watch`, `youtu.be`, and `shorts` link, including one
   with `t=90`, which should start at 90 s. Use the watch-page background menu
   separately and confirm that it uses the live playhead.
4. Open the popup: verify title/time updates, Pause, Resume, Stop, recasting the
   same video after Stop, and Cast this tab. Check a normal non-YouTube tab has
   Cast this tab disabled. Try `/shorts/`, `/live/`, `/embed/` and a timestamped
   URL when no content script is present; the latter should use its URL time.
5. Try Option+Shift+V and reassign it in Manage Extension Shortcuts. Confirm
   the active tab, not a different open YouTube tab, is cast.
6. Disconnect the Mac from the van LAN, open the popup and try the player
   button/shortcut. Expect “Van TV unreachable”, a readable error, no premature
   local pause, and no retry storm. Reconnect and confirm normal operation.
   With only `vanpi.lan` resolution unavailable, verify the LAN-IP fallback.
7. Start a cast and immediately navigate to another video. The newly opened
   video's playback must not be paused by the previous cast's acknowledgement.
