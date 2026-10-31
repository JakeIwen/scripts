# Source from .bashrc before the noninteractive Codex boundary.
vanpi-metered() { "$HOME/scripts/vanpi-metered" "$@"; }
isMetered() {
  local status
  "$HOME/scripts/vanpi-metered" is-metered "$@"
  status=$?
  # Only the explicit unmetered exit code permits an unknown installation.
  [[ "$status" != 1 ]]
}
meteredAllows() { "$HOME/scripts/vanpi-metered" allows "$@"; }
startor() { "$HOME/scripts/vanpi-metered" approve pi.torrents "${1:-1h}"; }
startorx() { "$HOME/scripts/vanpi-metered" revoke pi.torrents "$@"; }
