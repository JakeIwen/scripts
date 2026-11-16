# HDMI audio on the Pi

The Pi's audio goes out HDMI to a Sonos TV input through an HDMI audio splitter
and an optical link. Three pieces of this path are Pi-local system state, not
tracked in this repository and not deployed by any script here: the kernel
command line, an EDID file baked into the initramfs, and a boot unit. This
document records them so the state can be checked, rebuilt and undone.

Verified on `vanpi` on 2026-11-16.

## Path and stack

- Pi 4B `HDMI-A-1` -> HDMI audio splitter -> optical -> Sonos TV input.
- Pi OS Bookworm, `vc4-kms-v3d`, lightdm with labwc (Wayland, since 2026-10-29),
  PipeWire with WirePlumber 0.4.13.

## Why three pieces are needed

1. The splitter's own EDID does not advertise audio, so the Pi is given a stored
   EDID from an HP 22cwa monitor with audio added (piece 1).
2. The kernel command line forces the connector on. A forced connector is not
   probed at boot, and vc4 reports the ALSA "HDMI Jack" as plugged only from its
   detection path. With the jack reported unplugged, PipeWire marks
   `output:hdmi-stereo` unavailable, WirePlumber will not restore the saved
   profile, and there is no default sink: silence after every boot, with HDMI
   greyed out in the volume menu. A boot unit runs detection and re-forces the
   connector (piece 2).

Both pieces are required. The EDID patch alone was installed first, and a reboot
still showed the jack off.

## Kernel command line

`/boot/firmware/cmdline.txt` carries
`drm.edid_firmware=HDMI-A-1:edid/hp-22cwa.bin video=HDMI-A-1:D`, added
2026-08-11. The first part forces the stored EDID; the second forces the
connector on. This is why piece 2 exists.

## Piece 1: patched EDID

- `/usr/lib/firmware/edid/hp-22cwa.bin`, 256 bytes, sha256
  `39d83f930f0c4247f5ce0fb81a8965e1e69c52a7a1bec59b538c7dadb1c2481e`.
- Original, sha256 `4102564a60980b7ee209f87d32164c609c6cac3917d5fafb21bd08406a85b2b9`,
  kept at `/usr/local/share/hdmi-audio/hp-22cwa.orig.bin`. The producing script
  is `/usr/local/share/hdmi-audio/patch_edid.py`.

Changes, zero-based absolute offsets:

- Byte 130 (CTA detailed-timing offset) `19` -> `21`.
- Byte 131 `b1` -> `f1` (Basic Audio).
- Eight bytes `23 09 07 01 83 01 00 00` inserted at offset 153: stereo LPCM,
  32/44.1/48 kHz, 16-bit, plus a front left/right speaker allocation block.

The detailed timings shift by eight bytes and the trailing padding shrinks by
eight. The checksum stays `f6`. Video timings are unchanged.

The display driver loads the EDID before the root filesystem is mounted, so it
must sit inside the initramfs. `/etc/initramfs-tools/hooks/vanpi-edid` embeds it,
which keeps it across kernel upgrades. After any change to the file:

```
sudo update-initramfs -u -k "$(uname -r)"
```

Then confirm the embedded copy matches. The Pi boots `/boot/firmware/initramfs8`,
selected by `auto_initramfs` in `/boot/firmware/config.txt`.

## Piece 2: boot unit

- `/etc/systemd/system/hdmi-audio-jack.service`: oneshot,
  `After=display-manager.service`, `WantedBy=graphical.target`, enabled.
  Installed 2026-11-16.
- `/usr/local/sbin/hdmi-audio-jack`, sha256
  `9c7b8c949cb40e497e61fcc990aa3ebc197ec1222eb7c11011a0e3c96b7eee91`.

The script acts only while the kernel command line still forces `HDMI-A-1` on.
It takes a lock so one copy runs at a time, writes `detect` to the connector's
sysfs `status` file, then writes `on`, which leaves the connector forced on with
the jack reported plugged. It re-forces `on` on exit. `--restore`, the unit's
`ExecStopPost`, re-forces the connector if a run was killed.

Exit codes: `0` detected connected; `2` nothing answered on the cable (the jack
stays off for this boot; once the splitter is powered, run
`sudo /usr/local/sbin/hdmi-audio-jack` once); `1` failure.

The DRM card number changes between boots (`card0` on one, `card1` on the next),
so use `card*-HDMI-A-1` globs, never a fixed card path.

Fixture tests use scratch files and never touch `/sys`:

```
sh /usr/local/share/hdmi-audio/fixture_test.sh /usr/local/sbin/hdmi-audio-jack
```

## Checking after a boot

```
journalctl -b -u hdmi-audio-jack.service
```
Expect one line: "detected as connected; audio jack reported plugged, connector forced on again".

```
amixer -c vc4hdmi0 cget numid=1
```
Expect `values=on`. The card name `vc4hdmi0` resolves on this Pi.

```
XDG_RUNTIME_DIR=/run/user/1000 pactl list cards
```
Expect `alsa_card.platform-fef00700.hdmi` with `output:hdmi-stereo` available
(`available: yes`) and as the Active Profile.

```
XDG_RUNTIME_DIR=/run/user/1000 pactl get-default-sink
```
Expect `alsa_output.platform-fef00700.hdmi.hdmi-stereo`.

## Known limits

- A later hotplug signal from the splitter can still change the jack state while
  the connector is forced on.
- The labwc idle-blanking script powers the output off when idle
  (see [labwc idle blanking](../deployment.md#labwc-idle-blanking)), which stops
  HDMI audio while the screen is blanked.
- yt-cast bypasses PipeWire and plays to `alsa/hdmi:CARD=vc4hdmi0,DEV=0`
  directly, so its audio skips the default sink.

## Undo

```
sudo systemctl disable hdmi-audio-jack.service
sudo rm /etc/systemd/system/hdmi-audio-jack.service /usr/local/sbin/hdmi-audio-jack
sudo systemctl daemon-reload
sudo cp /usr/local/share/hdmi-audio/hp-22cwa.orig.bin /usr/lib/firmware/edid/hp-22cwa.bin
sudo update-initramfs -u -k "$(uname -r)"
```

The kernel command line is separate: removing the forcing arguments needs an edit
to `/boot/firmware/cmdline.txt` and a reboot.

Both changes were reviewed by GPT-6 Astra before install.
