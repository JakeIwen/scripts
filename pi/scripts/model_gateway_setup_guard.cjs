'use strict';
// Preloaded into Node processes on vanpi through NODE_OPTIONS (set in
// pi/.bashrc). Refuses Model Gateway's `setup` on this host: setup always
// downloads claude-code-proxy's release binary, which needs glibc 2.39 while
// Bookworm has 2.36, and it would replace the locally built proxy and take the
// gateway down. Every route into setup (the plugin CLI, the stable launcher,
// and the Toolshed updater's update.js) runs through `node`, so this one check
// covers them all. Everything else passes through untouched.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const EVAL_FLAGS = new Set(['-e', '--eval', '-p', '--print']);
// Compare real paths so symlinked homes, temp dirs, or plugin caches still match.
function real(file) {
  try {
    return fs.realpathSync(file);
  } catch {
    return path.resolve(file);
  }
}

const config = real(process.env.CLAUDE_CONFIG_DIR || path.join(os.homedir(), '.claude'));
const LAUNCHER = path.join(config, 'model-gateway', 'model-gateway.js');
const UPDATER = path.join(config, 'model-gateway', 'update.js');
// <config>/plugins/cache/<marketplace>/model-gateway/<version>/bin/model-gateway.js
const PLUGIN_CLI = /^plugins\/cache\/[^/]+\/model-gateway\/[^/]+\/bin\/model-gateway\.js$/;

function runsSetup() {
  // Under -e/-p, argv[1] is data for the evaluated code, not a script being run.
  if (process.execArgv.some((flag) => EVAL_FLAGS.has(flag.split('=')[0]))) return false;
  const [entry, command] = process.argv.slice(1);
  if (!entry) return false;
  const script = real(entry);
  if (script === UPDATER) return true;
  if (command !== 'setup') return false;
  return script === LAUNCHER || PLUGIN_CLI.test(path.relative(config, script).split(path.sep).join('/'));
}

if (process.env.MODEL_GATEWAY_ALLOW_SETUP !== '1' && runsSetup()) {
  process.stderr.write(`
model-gateway setup is blocked on vanpi.

setup downloads claude-code-proxy's release binary, which needs glibc 2.39;
this Pi runs Bookworm (glibc 2.36), so that binary cannot start. It would
replace the proxy built locally on this Pi and take the gateway down.

Instead:
  update or repair the proxy:  /home/pi/scripts/build_claude_code_proxy.sh
  start what is down:          node ~/.claude/model-gateway/model-gateway.js ensure
  sign in again:               node ~/.claude/model-gateway/model-gateway.js login --device
  rewire Claude Code:          node ~/.claude/model-gateway/model-gateway.js env --write-user
  check health:                node ~/.claude/model-gateway/model-gateway.js doctor

Details: pi/docs/compute/CLAUDE_MODEL_GATEWAY.md in the scripts repo.
Override (only once upstream ships a Bookworm-compatible build):
  MODEL_GATEWAY_ALLOW_SETUP=1 node ... setup
`);
  process.exit(3);
}
