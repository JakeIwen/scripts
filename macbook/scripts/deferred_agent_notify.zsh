#!/bin/zsh
# Sends an add-agent job's result (stdin) to the phone: one ntfy titled with the
# job's name, on the topic its agent.env names. Both agent run scripts (Mac and
# vanpi) call it, so the notification looks the same whichever host ran the prompt.
# usage: deferred_agent_notify.zsh JOB_DIR < BODY
set -u -o pipefail
source $1/agent.env
exec /usr/bin/python3 -I -B $agent_notifier --secrets $agent_secrets \
  --topic-var $agent_topic_var --title $agent_name --priority default --tags robot
