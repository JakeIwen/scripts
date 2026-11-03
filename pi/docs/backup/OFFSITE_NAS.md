# Offsite NAS receiver and remote runs

This is an **on-demand** extension of [the local seed tool](OFFSITE.md). It changes
neither the four source selections nor the iCloud jobs, their settings, cadence or
uplink permissions. Get the link working and collect run/check/restore evidence
before deciding those policies. The 5 TB `FORT-MPLS-5TB` is priority 10; a separately
seeded 14 TB drive uses priority 20. Never deploy this cohort while either local
fill unit is running. There is no automatic initialization, erase, prune or retention.

## Installation prerequisites

The NAS model, DSM version and CPU must first be discovered by its administrator.
There is no account-free installer and no Container Manager dependency. Jacob's
DSM administrator account, SSH, the DSM **Python 3.9+ package** and the native DSM
Tailscale package are explicit prerequisites. This version supports Linux
`x86_64`, `aarch64`, `i686` and `armv7l`; unsupported architectures fail rather than
selecting a similar binary. DSM must support `/proc/self/fd`, `/proc/self/mountinfo`,
`flock`, parent-death signals and the seeded ext4 filesystem features. These Linux/
DSM contracts still need a hardware smoke once access arrives. Do not enable
access to the friend's shares; the receiver needs only its dedicated install
folder and mounted marked USB drives. A DSM administrator is not a security sandbox.

Read-only discovery on the NAS, using the installed Python and Tailscale paths:

```sh
uname -srm
python3 --version
command -v python3
command -v tailscale
cat /etc.defaults/VERSION
cat /proc/self/mountinfo
```

If DSM does not put these package executables on PATH, resolve their actual
package paths explicitly. No download or install occurs at boot. Choose a fixed
unused unprivileged port (the examples use **8088**) after inspecting actual NAS
listeners. Do not copy this machine's IP or guess the NAS CPU from its model name.

### Native binary and files

Stage only `synology/offsite/` Python source and
`pi/scripts/backup/offsite_config.py`, `pi/scripts/backup/protected_json.py`, keeping
those repository-relative paths. Do not transfer secret directories, the encryption
password, or the whole private scripts tree. From that staging root on the NAS:

```sh
sudo python3 -m synology.offsite.install --prepare
sudo python3 -m synology.offsite.install
```

The explicit first installer requires an empty real `/volume1/vanpi-offsite`
directory, root-owned mode 0700 (or an explicitly selected dedicated `--root`). It
hash-verifies the archive, extracts only the native binary and checks its version
before installing it. It refuses to overwrite an existing receiver. Installation
is **not** configuration, Task Scheduler registration or a successful listening
server. A failed install can leave a partial private directory; inspect it, do not
reuse or delete it blindly. Updates should be staged at a new private root only
after stopping/verifying the old one; never replace code beneath a running watcher.

Rest-server **0.14.0** is pinned in `synology/offsite/binaries.py`. SHA-256 values
come from the official release's
[SHA256SUMS](https://github.com/restic/rest-server/releases/download/v0.14.0/SHA256SUMS),
retrieved 2026-11-03, not from guessed architecture mappings. The installed
`binary.json` records archive and decompressed-binary hashes; the watcher verifies
them and the current architecture. The Pi keeps its existing restic **0.19.1** pin.

### Private configuration and accounts

Generate a **new**, random, URL-safe transport password of at least 32 random bytes
(e.g. `secrets.token_urlsafe(48)` in a private setup script). This is unrelated to
the restic encryption password. Store it privately on the Pi and NAS, never in an
HTTP URL, command argument, shell history, marker or status output.

**Never copy `/root/.config/restic/vanpi-offsite.password` to the NAS.** Only the Pi
and the independent recovery/password-manager copy hold that encryption secret.
The NAS cannot decrypt snapshots or completion receipts. Keep `repos.json` with
the Pi recovery material too; it contains only previously seeded known repository
identities. Remote operation cannot create or register new ones.

Create `/volume1/vanpi-offsite/config.json`, root-owned 0600, using `sudoedit`.
Replace the example IP, executable path and password; this is a schema example,
not a usable account or credential:

```json
{"schema":1,"address":"100.64.0.10","port":8088,"transport_password":"REPLACE_WITH_SEPARATE_RANDOM_TRANSPORT_SECRET","tailscale":"/actual/resolved/path/to/tailscale"}
```

The listener accepts a literal Tailscale IPv4 address only, never wildcard,
loopback, LAN, DNS or a public address. The configured IP must be this running
Tailscale node's own address. Use a tailnet ACL granting only the Pi access to the
NAS receiver port and the intended admin access; block NAS-initiated access to
Jacob's other devices. Review tag ownership/key expiry with the administrator.
Do not enable router forwarding, UPnP, Funnel, a public reverse proxy or DSM
internet publication. HTTP Basic is carried inside Tailscale's encrypted link;
no public HTTP route is supported. NAS firewall changes are a separate reviewed
administrative operation, not something this installer performs.

On the Pi, create `/var/lib/vanpi-offsite/remote.json`, root-owned 0600, with the
same transport secret, NAS Tailscale IP and port, **without** the `tailscale` key:

```json
{"schema":1,"address":"100.64.0.10","port":8088,"transport_password":"REPLACE_WITH_SEPARATE_RANDOM_TRANSPORT_SECRET"}
```

Private configuration reads are bounded, no-follow, metadata-checked descriptor
reads through the existing `protected_json.py`. The Pi rejects reuse of its
restic encryption password as the transport password. No password appears in
restic argv; only controlled `RESTIC_REST_USERNAME`/`RESTIC_REST_PASSWORD` child
environment values are allowed after all ambient `RESTIC_*` values are cleared.
Ambient HTTP proxies are also removed for the remote transport.

### DSM boot and persistent stop

Installed commands run **from the private installation root**. Replace `python3`
with the absolute verified DSM package interpreter in scheduled tasks:

```sh
cd /volume1/vanpi-offsite
sudo python3 -B -m receiver prepare
sudo python3 -B -m receiver start
sudo python3 -B -m receiver status
sudo python3 -B -m receiver stop
sudo python3 -B -m receiver restart
```

`prepare` validates only. `start` enables and starts one watcher; it is not proof
that a drive is being served. `status` exits zero only for an enabled serving
watcher with matching boot/PID/start-time identities for watcher and server.
Waiting, error and stopped states are not healthy installs. `stop` durably disables
before waiting for the singleton lock, so boot cannot undo it. Stop preserves
configuration, binaries and all USB data. It does not guess or signal stale PIDs.

Register a **root DSM Task Scheduler boot task** with this command (actual absolute
interpreter path required):

```sh
cd /volume1/vanpi-offsite && /actual/python3 -B -m receiver boot
```

Also register the same command as a receiver watchdog task at a short interval
(e.g. every minute). It starts only an enabled missing watcher; it never launches
a Pi backup, grants bandwidth permission or overrides a persistent stop. DSM has
no portable supported Task Scheduler CLI across the unknown versions, so creation
and enabled-state verification require the administrator's DSM interface. No
systemd unit is assumed. The installer deliberately does not claim to register
these tasks. Test the boot command, intentional stop followed by that command,
and a real reboot before calling boot registration verified.

The watcher holds one non-replaced flock inode, owns/reaps its child, restarts a
failed child only after revalidation, and stops it on unplug, priority switch,
Tailscale/config/mount errors or shutdown. The child inherits the singleton lock
and a Linux parent-death SIGKILL fence, so an abruptly killed watcher cannot leave
a second live server while a replacement starts. Private `watch.log` contains no
transport credentials or rest-server request debug logging. Errors expose only
fixed diagnoses; inspect the explicit config/dependencies rather than assuming
waiting means backup success.

## Drive selection and immediate switch fencing

Discovery reads the kernel mount inventory **before** touching paths. Only exact
read-write ext4 filesystem-root mounts matching `/volumeUSB*/usbshare*` are eligible.
Absent mounts never become directories to create or initialize. Unmarked USB
mounts are ignored; malformed markers, duplicate mount/repository identities and
tied highest priorities fail closed. Only the unique highest-priority valid drive
is selected. It must contain the existing `vanpi-restic/{config,data,index,keys,
locks,snapshots}` layout without symlinks, nested filesystems or special entries.
There is no walk of the friend's shares.

Two independent fences prevent requests for repository A from landing in B:

1. The child inherits an open descriptor for the selected repository directory.
   Its path is `/proc/self/fd/N`, **not** the reusable USB mountpoint. Even between
   watcher polls, an unplug/mount replacement cannot resolve into NAS internal
   storage or another disk. Failed I/O stays failed on the original filesystem.
2. Each child has an htpasswd file containing **only the selected full repository
   ID as username** and the random transport-password hash. A switch first stops
   and reaps the old child, then writes the new auth file and starts the new child.
   It never relies on hot-reload timing or credential caches. A stale A client is
   rejected with 401 by B immediately, before any periodic Pi guard can run.

Tagged upstream sources verified for these choices:
[restic environment auth](https://github.com/restic/restic/blob/v0.19.1/internal/backend/rest/config.go),
[rest-server per-request auth](https://github.com/restic/rest-server/blob/v0.14.0/mux.go),
[hash support/cache](https://github.com/restic/rest-server/blob/v0.14.0/htpasswd.go),
[path construction](https://github.com/restic/rest-server/blob/v0.14.0/handlers.go) and
[append-only writes/deletion](https://github.com/restic/rest-server/blob/v0.14.0/repo/repo.go).
`{SHA}` is supported by this release; its unsalted hash is used only for a
high-entropy random transport secret, never a human password. The server is always
started `--append-only`, authenticated, without request/debug logs or `--no-auth`.

The watcher never ejects/unmounts disks. Stop it before an administrative ordinary
DSM eject, verify the mount is absent, then unplug. Never force/lazy unmount. To
upgrade: seed/register the 14 TB drive on the Pi first, mail it, plug it into DSM,
verify status selects its priority-20 ID, then stop/eject the old drive cleanly.
Selecting the new Pi `--label` remains explicit; an old-label run fails rather
than silently following the new disk.

## On-demand Pi operation and metrics

Deploy the new flat backup cohort through its owning targeted installer and the
metered consumer registry through the package owner **after local fills finish**.
`pi.offsite_backup` is a separate registered guard consumer; no approval or
exemption is installed. Metered or unknown uplinks are denied by default. This
feature does not borrow iCloud permissions or alter them. Every remote restic
operation (including config discovery, checks, receipt reads and restore tests)
collects fresh existing uplink evidence and asks `vanpi-metered admit
pi.offsite_backup`, checks a running Tailscale client and the configured peer's
online state and a kernel route through the Pi's `tailscale0`, then repeats during
and after the operation. Lost/unknown/denied
admission interrupts the process; there is no automatic retry. Guard polling uses
the existing five-second cadence plus bounded collector/CLI execution time, not
a promise of instantaneous cost cancellation. The immediate drive fence is
server-side and does not depend on this cadence.

The existing **exclusive legacy fd 9 lock** remains the whole-run admission fence,
including checks/restore tests; it already excludes both cloud-upload-slot holders
and other backups. No second slot is acquired, no lock ordering is inverted, and
no runtime backup lock file is invented. Ignition, exact source mounts, no-cache,
Samba drain/zero handles/final image inventory and local cleanup remain unchanged.
The remote target is never locally mounted or unmounted. Plain local `backup` and
`check` still work without NAS config, Tailscale or remote permission tools.

```sh
sudo /home/pi/scripts/backup/offsite_backup.sh backup \
  --remote --label FORT-MPLS-5TB --only borg
sudo /home/pi/scripts/backup/offsite_backup.sh check \
  --remote --label FORT-MPLS-5TB --read-data-subset 5%
sudo /home/pi/scripts/backup/offsite_backup.sh receipts --label FORT-MPLS-5TB
sudo /home/pi/scripts/backup/offsite_backup.sh status
```

Use the local runbook's transient systemd-run wrapper for long operations and its
Time Machine release procedure before `--only time-machine`. No new daemon or
backup timer is installed. `status.json` saves duration, overall/per-source result,
full source and receipt IDs, and per-source `logical_bytes_processed` versus
`newly_added_packed_bytes` from restic's summary. Packed bytes exclude already
present deduplicated data and are **not measured wire bytes** (HTTP/encryption and
retry overhead are not included). Progress `bytes_done`, `rate` and ETA describe
logical source processing, not internet throughput. Separate latest
`remote-check.json` and `remote-restore-test.json` preserve success/failure,
selection and duration evidence without overwriting backup status. These are
latest records, not an unbounded run-history log. One existing ntfy reports backup
outcome; CLI check/readback errors remain explicit and are recorded separately.

## Completion and recovery

**Never treat an arbitrary remote snapshot as a completed recovery point.** Every
remote source snapshot retains `offsite-incomplete`. Only after an error-free
backup and all source consistency checks (including the final Time Machine image
inventory) does the Pi create a tiny encrypted stdin snapshot tagged
`offsite-receipt-v1`. Its `/offsite-completion.json` contains schema, registered repo
ID, source, full source snapshot ID, completion time and the two distinct byte
metrics. The Pi reads that receipt back before reporting success. No tag rewrite,
forget, delete or prune is attempted remotely. An interrupted/changed source has
no completion receipt. A failure after a receipt was committed may still leave a
valid per-source receipt; inspect it, not just the last overall run outcome.

**Pinned upstream caveat:** restic 0.19.1 `tag --remove` against rest-server 0.14.0
append-only can exit **zero** while warning about HTTP 403 and saying no snapshots
were modified. This is not completion. Local v1 also checks the exact changed-ID
record; v2 avoids the command entirely. Forbidden deletion is never reclassified
as a successful tag operation.

`receipts` validates the encrypted record, matching registered repository, exact
source identity and source metadata; it does not trust a receipt tag alone. Use a
full receipt ID from its output and a small existing absolute path in that source
for a verified readback into a **separate, empty, real directory**:

```sh
sudo mkdir -m 700 /var/tmp/offsite-restore-test
sudo /home/pi/scripts/backup/offsite_backup.sh restore-test \
  --label FORT-MPLS-5TB --receipt FULL_RECEIPT_SNAPSHOT_ID \
  --path /mnt/bigboi/borg/vanpi-encrypted/README \
  --target /var/tmp/offsite-restore-test
```

Select a path actually in the receipt's source snapshot (the example is not a
promise that Borg provides that file). Empty/nonmatching restores fail. The CLI
runs restic `restore --verify`; readback is bandwidth-admitted just like upload.
It never restores over a live source. For disaster recovery, provision the restic
password and known repository records on a trusted recovery machine, validate and
extract the encrypted receipt first, then restore **its full source snapshot ID**
to a separate destination with restic 0.19.1 `restore --verify`. The NAS must never
be given the password to do this. Before reconnecting a Time Machine image, restore
the entire receipt-backed sparsebundle and use the independently saved image
password. Native Borg restore likewise requires its independent passphrase.
Local-v1 seed snapshots whose incomplete tag was successfully removed remain
local-v1 recovery points; they are not falsely advertised as new v2 receipts.

## Verification

The ordinary suite exercises private config, known-ID admission/refusal, local-v1
independence, mounted-drive selection and real-process watcher ownership/switch/
error/stop behavior. Opt-in acceptance uses disposable loopback repositories and
random test ports, never live devices or credentials. Set `OFFSITE_RESTIC` and
`OFFSITE_REST_SERVER` to **isolated, archive-verified** restic 0.19.1 and rest-server
0.14.0 binaries; run `pi.tests.backup.test_offsite_rest_integration` through the
repository test runner's import-root environment. It covers real authenticated
append-only backup, forbidden tagging, check/read-data, receipt extraction,
verified restore, immediate A-to-B auth refusal, admission interruption and a
changed Time Machine image. The test skip in the default suite explicitly names
these opt-in binary requirements. The Mac arm64 acceptance archive checksums are:

- restic: `7be0a144ccc377880f294204aa271d76e4b79554b42a751151d425ce6ebac143`
- rest-server: `057004ce24ca9edfbe0e391eae87981ab03f2abb6b65cf8fdfcddf847ded8656`

Before real deployment is called verified, run the Linux descriptor/mount and
parent-death smoke, native DSM install/version/status, denied authentication,
actual USB unplug/switch/ordinary-eject, persistent stop/boot/reboot, Pi metered
hold/interruption, and a small receipt-backed remote backup/check/restore. Verify
the 5 TB ext4 mount on the actual DSM before mailing or relying on the 14 TB drive.
No such hardware install, live transfer, grant or reboot is implied by Mac tests.
