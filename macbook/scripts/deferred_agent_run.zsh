#!/bin/zsh
# Run script of a job queued by `deferred.zsh add-agent`: waits for its --ready
# condition, runs one headless Claude session with the saved prompt, and sends
# the outcome to the phone. Copied into the job directory, whose agent.env and
# prompt.txt hold the job's settings, so the job stands alone.
set -u -o pipefail
job=${0:A:h}
source $job/agent.env
export PATH=$HOME/.local/bin:$PATH   # the native claude CLI; the job PATH lacks it
not_ready=75                         # deferred.zsh: condition not met, retry later
summary_limit=600

cd -- $agent_cwd || exit 1

preamble='You are running headless from a deferred job, not a live chat. Your final reply is sent to the
user'"'"'s phone as a notification: write at most 4 short lines, and make the first line exactly "OK" or
"NEEDS LOOK". Do not send any ntfy or other notification yourself. Do not add a Terms block or any
DONE or IN FLIGHT marker.

Task:'

# A session that finished but could not be reported is not run again on retry,
# and delivering its result does not wait for --ready.
if [[ ! -s $job/claude.json ]]; then
  if [[ -n $agent_ready ]]; then
    /bin/zsh -c $agent_ready || exit $not_ready
  fi
  { print -r -- $preamble; print; /bin/cat $job/prompt.txt } |
    claude -p --output-format json --model $agent_model --effort $agent_effort \
      --permission-mode bypassPermissions > $job/claude.json.tmp || {
    rc=$?
    print -u2 -- "claude exited $rc"
    /bin/mv -f $job/claude.json.tmp $job/claude.json.failed 2>/dev/null
    exit $rc
  }
  /bin/mv -f $job/claude.json.tmp $job/claude.json
fi

body=$(/usr/bin/python3 -I -B - $job/claude.json $agent_cwd $summary_limit <<'PY'
import json
import shlex
import sys

path, cwd, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
data = json.load(open(path, encoding="utf-8"))
if isinstance(data, list):
    data = next((item for item in reversed(data) if isinstance(item, dict) and item.get("type") == "result"), {})
if not isinstance(data, dict) or data.get("is_error") is not False:
    sys.exit(3)
result = str(data.get("result") or "").strip()
if len(result) > limit:
    result = result[:limit].rstrip() + "..."
session = data.get("session_id")
if isinstance(session, str) and session:
    tail = f"Open it: cd {shlex.quote(cwd)} && claude --resume {session}"
else:
    tail = "No session id; see last.log in Mac Panel."
print(f"{result}\n\n{tail}")
PY
) || { print -u2 -- "claude reported an error or unreadable output"; /bin/mv -f $job/claude.json $job/claude.failed.json; exit 1 }

print -r -- $body | /usr/bin/python3 -I -B $agent_notifier --secrets $agent_secrets \
  --topic-var $agent_topic_var --title $agent_name --priority default --tags robot
