#!/bin/zsh
# A temporary GUI job proves TCC attribution without installing a login item.
set -eu -o pipefail

source_dir=${0:A:h}
repo=${source_dir:h:h:h}
work=$(/usr/bin/mktemp -d "${TMPDIR:-/tmp/}metered-wifi-verify.XXXXXX")
label="com.jacobr.metered-wifi.verify.$$"
domain="gui/$(/usr/bin/id -u)"
loaded=0
cleanup() {
    if (( loaded )); then
        /bin/launchctl bootout "$domain/$label" || {
            print -u2 -r -- "Could not unload $domain/$label; retained $work"
            return 1
        }
    fi
    /bin/rm -rf -- "$work"
}
trap cleanup EXIT

/usr/bin/python3 -I - "$repo" "$work" "$label" <<'PY'
from pathlib import Path
import plistlib
import sys
repo, work, label = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
plist = {
    "Label": label,
    "ProgramArguments": ["/usr/bin/python3", "-B", "-m",
                         "macbook.metered.wifi_helper.probe_launchagent", str(work / "result.json")],
    "WorkingDirectory": str(repo),
    "EnvironmentVariables": {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(repo)},
    "RunAtLoad": True,
    "ProcessType": "Background",
    "Umask": 63,
    "StandardOutPath": str(work / "stdout.log"),
    "StandardErrorPath": str(work / "stderr.log"),
}
(work / "agent.plist").write_bytes(plistlib.dumps(plist))
PY
/usr/bin/plutil -lint "$work/agent.plist"
/bin/launchctl bootstrap "$domain" "$work/agent.plist"
loaded=1
deadline=$(( SECONDS + 40 ))
while [[ ! -f "$work/result.json" ]]; do
    if (( SECONDS >= deadline )); then
        /bin/launchctl print "$domain/$label"
        /usr/bin/python3 -I -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())' "$work/stderr.log"
        print -u2 'LaunchAgent verification timed out.'
        exit 1
    fi
    /bin/sleep 0.2
done
/usr/bin/python3 -I - "$work/result.json" <<'PY'
import json
import pathlib
import sys
result = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(json.dumps(result, indent=2, sort_keys=True))
if not result["success"]:
    print("Allow Metered Wi-Fi in Location Services, then rerun this check.")
sys.exit(0 if result["success"] else 1)
PY
cleanup
loaded=0
trap - EXIT
print 'PASS: temporary LaunchAgent unloaded; its plist and output files removed.'
