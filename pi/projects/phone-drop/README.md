# Phone Drop

An AirDrop substitute for the van: an iPhone share-sheet Shortcut uploads
photos, videos and files to vanpi over HTTP. Real AirDrop needs Apple's AWDL
radio protocol, which the Pi's Wi-Fi chip cannot speak.

| | |
|---|---|
| Upload URL (van Wi-Fi) | `http://vanpi.lan:8793/upload` |
| Upload URL (elsewhere, via Tailscale) | `http://<tailscale ip -4 vanpi>:8793/upload` |
| Inbox on the Pi | `/home/pi/Documents/agent-shared/from-phone/` |
| Inbox on the Mac | `/Volumes/pihome/Documents/agent-shared/from-phone/` |
| Token | `/home/pi/.config/phone-drop/token` (mode 600, never committed) |
| Service | `phone-drop.service`, code in `/home/pi/phone-drop/` |

## Behavior

- `POST /upload` needs `Authorization: Bearer <token>`. It saves every file part
  of a `multipart/form-data` body, or a raw body named by the `X-Filename`
  header or `?name=` query parameter. Responses are one line of plain text
  (`Saved IMG_3454.HEIC (2.0 MB)`) so the Shortcut can show them as-is.
- `GET /health` is public and returns `{"ok": true}`.
- Names are reduced to one visible path component. An existing file is never
  overwritten; the new one becomes `name (2).ext`.
- Files stream into the hidden `.incoming/` directory and appear in the inbox
  only once complete.
- The inbox lives on the SD card, so uploads are refused when the inbox would
  exceed 8 GiB or leave less than 8 GiB free on the root filesystem. That keeps
  the card below the backup watchdog's root-usage limit. Move files out to
  make room.
- One upload runs at a time. A connection idle for 120 seconds is dropped.

## Deploy

```bash
bash pi/projects/phone-drop/deploy.sh
```

The script is idempotent. It creates the inbox and a token only when missing,
restarts the service only when the code or unit changed, and then checks that
health responds and a wrong token is refused. It never prints the token.
`PHONE_DROP_TARGET` overrides the default `pi@vanpi.lan`. The broad
`pi/sync_scripts.sh` does not touch these files.

Tests run against a real server on a random port:

```bash
python3 -m unittest discover -s pi/projects/phone-drop
```

They need Werkzeug 2.2, which the Pi has as `python3-werkzeug`.

## iPhone Shortcut

Copy the header value to the Mac clipboard, then paste it on the iPhone through
Universal Clipboard within about two minutes:

```bash
ssh vanpi.lan 'printf "Bearer %s" "$(cat .config/phone-drop/token)"' | pbcopy
```

Build a Shortcut named **Send to vanpi**:

1. In the shortcut's details (the ⓘ button), turn on **Show in Share Sheet**.
   In the **Receive** block at the top, accept only Images, Media, Files and
   PDFs. Set "If there's no input" to **Ask For Photos** if you also want to
   run it from the Shortcuts app.
2. **Get Network Details**: Network Name of Wi-Fi.
3. **If** Network Details **is** `dendelion`:
   **Text** `http://vanpi.lan:8793/upload`.
   **Otherwise**: **Text** `http://<tailscale ip>:8793/upload`.
   **End If**.
4. **Repeat with Each** item in **Shortcut Input**:
   **Get Contents of URL** with URL = **If Result**, Method **POST**,
   header `Authorization` = the pasted `Bearer …` value, Request Body
   **Form**, one **File** field with key `file` and value **Repeat Item**.
5. After **End Repeat**: **Combine Text** of **Repeat Results** with New
   Lines, then **Show Notification** with the Combined Text.

The first run asks for Local Network access and for permission to send items
to the server; allow both. The off-LAN branch needs the Tailscale app connected.
Tailscale has relayed phone traffic through its servers rather than connecting
directly, so large videos sent off the van Wi-Fi use the uplink's data.
