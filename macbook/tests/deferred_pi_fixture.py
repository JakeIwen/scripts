"""Fake vanpi for the `add-agent --host vanpi` tests: a stand-in pi_agent.py and
ssh under a temporary HOME, driven by files in one state directory.

The job runs under `env -i`, so both fakes find that directory from a baked-in
path. Nothing here reaches the real Pi.
"""
import datetime
import json
import re
from pathlib import Path

from macbook.tests.deferred_fixture import DeferredHarness

YESTERDAY = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

# Mirrors pi_agent.py's exit codes and the report shapes remote.sh prints.
# Controls (files in FAKE): unreachable, launch-mode (ok | lost | lost-nothing |
# refuse), next-state (running | done | died), next-exit, next-final,
# next-error, stop-mode (ok | ignore), status-mode (normal | empty), stop-lag
# (status reads that still say running after a stop, then the run ends; 143),
# empty-reads (status reads after a stop that print nothing, like a dropped link),
# status-delay (seconds every status call sleeps first), extra-runs (that many
# newer runs listed before the real ones, as on a busy Pi).
FAKE_LAUNCHER = r'''
import json, secrets, sys, time
from pathlib import Path
FAKE = Path(@FAKE@)
RUNS = FAKE / "runs"
RUNS.mkdir(exist_ok=True)
args = sys.argv[1:]
with open(FAKE / "launcher-calls", "a") as log:
    log.write(json.dumps(args) + "\n")

def control(name, default=""):
    path = FAKE / name
    return path.read_text().strip() if path.exists() else default

if (FAKE / "unreachable").exists():
    print("pi-agent: can't reach vanpi; nothing was started", file=sys.stderr)
    sys.exit(5 if args[0] == "run" else 2)

def load(run_id):
    path = RUNS / (run_id + ".json")
    if not path.exists():
        print("pi-agent: no run %s in /home/pi/.agent/pi-agent-runs on vanpi" % run_id, file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text())

def option(name, default=""):
    return args[args.index(name) + 1] if name in args else default

if args[0] == "run":
    mode = control("launch-mode", "ok")
    if mode == "refuse":
        print("pi-agent: no directory /nope on vanpi", file=sys.stderr)
        sys.exit(2)
    name = option("--name")
    run_id = time.strftime("%m%d-%H%M%S-") + secrets.token_hex(2)
    if name:
        run_id += "-" + "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")[:30]
    prompt = Path(option("--prompt-file")).read_text()
    if mode != "lost-nothing":
        (FAKE / "prompts").mkdir(exist_ok=True)
        (FAKE / "prompts" / run_id).write_text(prompt)
        (RUNS / (run_id + ".json")).write_text(json.dumps({
            "state": control("next-state", "running"), "exit": control("next-exit", "0"),
            "final": control("next-final", "OK\nall good"), "error": control("next-error")}))
    if mode in ("lost", "lost-nothing"):
        print("pi-agent: can't reach vanpi; nothing was started", file=sys.stderr)
        sys.exit(5)
    print("started %s (claude, agent %s) in %s on vanpi" % (run_id, option("--agent"), option("--cd")))
    print("waiting is up to you: pi_agent.py wait %s" % run_id)
    sys.exit(0)

if args[0] == "status":
    time.sleep(float(control("status-delay", "0")))

if args[0] == "status" and (len(args) == 1 or args[1] == "-n"):
    limit = int(args[2]) if len(args) > 2 else 15
    lines = ["0101-000000-%04x-newer d" % i for i in range(int(control("extra-runs", "0")))]
    lines += ["%-28s %-8s claude sol   medium first line" % (path.stem, json.loads(path.read_text())["state"])
              for path in sorted(RUNS.glob("*.json"), reverse=True)]
    print("\n".join(lines[:limit]))
    sys.exit(0)

if args[0] == "status":
    run = load(args[1])
    empty = int(control("empty-reads", "0"))
    if empty > 0 and run.get("lag") is not None:
        (FAKE / "empty-reads").write_text(str(empty - 1))
        sys.exit(0)
    if control("status-mode") == "empty":
        sys.exit(0)
    if run.get("lag") is not None:
        if run["lag"] > 0:
            run["lag"] -= 1
        else:
            run.update(state="done", exit="143")
            del run["lag"]
        (RUNS / (args[1] + ".json")).write_text(json.dumps(run))
    print("run:     %s on vanpi" % args[1])
    print("agent:   sol via claude, in /home/pi")
    print("effort:  medium")
    if run["state"] == "done":
        print("state:   done, exit %s, 1m00s" % run["exit"])
    else:
        print("state:   %s, 1m00s" % run["state"])
    if run.get("error"):
        print("error:   %s" % run["error"])
    if run["state"] == "running":
        print("--- latest message so far")
        print("(nothing yet)")
    else:
        print("--- final message")
        print(run.get("final") or "(none)")
    sys.exit(0)

if args[0] == "stop":
    run = load(args[1])
    if run["state"] != "running":
        print("pi-agent: run %s isn't running" % args[1], file=sys.stderr)
        sys.exit(2)
    if control("stop-mode", "ok") != "ignore":
        if control("stop-lag"):
            run["lag"] = int(control("stop-lag"))
        else:
            run.update(state="done", exit="143")
        (RUNS / (args[1] + ".json")).write_text(json.dumps(run))
    print("stopping %s" % args[1])
    sys.exit(0)
sys.exit(2)
'''

# Runs the remote command locally with /bin/sh, as the Pi's login shell would.
FAKE_SSH = r'''#!/bin/sh
FAKE=@FAKE@
echo "$*" >> "$FAKE/ssh-calls"
[ -e "$FAKE/unreachable" ] && { echo "ssh: connect to host vanpi: timed out" >&2; exit 255; }
while :; do
  case $1 in
    -T) shift ;;
    -o) shift 2 ;;
    *) break ;;
  esac
done
shift
exec /bin/sh -c "$*"
'''


class PiHarness(DeferredHarness):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fake-pi"
        self.fake.mkdir()
        home = self.tmp / "home"
        skill = home / ".claude" / "skills" / "pi-agent"
        skill.mkdir(parents=True)
        (skill / "pi_agent.py").write_text(FAKE_LAUNCHER.replace("@FAKE@", repr(str(self.fake))))
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        ssh = bin_dir / "ssh"
        ssh.write_text(FAKE_SSH.replace("@FAKE@", "'%s'" % self.fake))
        ssh.chmod(0o755)
        self.env.update(HOME=str(home))
        self.prompt = self.tmp / "prompt.md"
        self.prompt.write_text("Check the Pi thing.\n")

    def control(self, name, value=""):
        (self.fake / name).write_text(value)

    def clear(self, name):
        (self.fake / name).unlink(missing_ok=True)

    def quick_stop_polling(self, job, window="0.5"):
        """Shrinks the stop-confirmation timings so tests do not wait: in the job's own
        copy (its passes) and, through the environment, for the scheduler's cancel."""
        remote = job / "deferred_pi_remote.py"
        source = remote.read_text()
        patched = re.sub(r"^STOP_CONFIRM_WINDOW, STOP_CONFIRM_INTERVAL, STOP_MIN_READ = .*$",
                         f"STOP_CONFIRM_WINDOW, STOP_CONFIRM_INTERVAL, STOP_MIN_READ = {window}, 0.02, 10",
                         source, flags=re.M)
        assert patched != source, "stop-confirmation constants not found in the job's remote module"
        remote.write_text(patched)
        self.env.update(DEFERRED_PI_STOP_WINDOW=window, DEFERRED_PI_STOP_INTERVAL="0.02")

    def age_launch(self, job, seconds):
        """Moves the saved launch time of the job's run back by SECONDS."""
        run = self.record(job, "remote-run.json")
        (job / "remote-run.json").write_text(json.dumps({**run, "launched": run["launched"] - seconds}))

    def set_meta(self, job, **changes):
        """Rewrites fields of the scheduler's job record, as a crash or an old version might leave it."""
        meta = {**self.meta(job), **{key: str(value) for key, value in changes.items()}}
        (job / "job").write_text("".join(f"{key}={value}\n" for key, value in meta.items()))

    def scheduler_log(self):
        return (self.root / "deferred.log").read_text()

    def offline(self, on=True):
        self.control("unreachable") if on else self.clear("unreachable")

    def add_pi(self, *args, prompt=None, name="pijob", due=YESTERDAY, check=True):
        result = self.deferred("add-agent", "--host", "vanpi", "--timeout", "600", *args, due, name,
                               str(prompt or self.prompt), check=check)
        return Path(result.stdout.strip()) if check else result

    def calls(self, verb=None):
        path = self.fake / "launcher-calls"
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [call for call in calls if verb is None or call[0] == verb]

    def runs(self):
        return sorted(path.stem for path in (self.fake / "runs").glob("*.json"))

    def set_run(self, run_id, **changes):
        path = self.fake / "runs" / (run_id + ".json")
        path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))

    def record(self, job, name):
        path = job / name
        return json.loads(path.read_text()) if path.exists() else None

    def last_log(self, job):
        return (job / "last.log").read_text()
