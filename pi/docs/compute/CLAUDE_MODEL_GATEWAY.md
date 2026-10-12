# Claude Code Model Gateway on the Pi

Model Gateway (`model-gateway@eigenwise-toolshed`, user scope) adds ChatGPT/Codex
models to Claude Code's `/model` picker by routing `claude-gpt-*` requests through a
local `claude-code-proxy`. Wiring is user-level (`~/.claude/settings.json` `env`),
the same as on the Mac.

## The proxy is built locally

The project's prebuilt `linux-arm64` binary needs glibc 2.39, and Bookworm ships 2.36,
so the downloaded binary exits immediately. `pi/scripts/build_claude_code_proxy.sh`
builds the release tag natively (linking against the Pi's glibc) and installs it at
`/home/pi/.claude/model-gateway/bin/claude-code-proxy`, keeping the previous binary
as `.prev`. It installs a minimal Rust toolchain on first run, builds at nice 19 with
idle I/O priority, needs 4 GB free on `/home/pi`, and deletes its build directory
afterwards.

**The gateway's `setup` is blocked on the Pi.** It always downloads the release binary
and would replace the local build. `pi/scripts/model_gateway_setup_guard.cjs` is
preloaded into every Node process started from a Pi shell (`pi/.bashrc` exports it in
`NODE_OPTIONS`, for interactive and SSH shells and the Claude Code sessions they start),
and refuses the plugin CLI's `setup`, the stable launcher's `setup`, and the Toolshed
updater's `update.js` with an explanation and exit code 3. `ensure`, `login`, `env`,
`doctor`, and `status` never download and pass through. A process started outside a Pi
shell (cron, systemd) is not covered; nothing there runs setup today.
`MODEL_GATEWAY_ALLOW_SETUP=1` overrides the guard once upstream ships a compatible
build, at which point remove the guard and this section. Tests:
`bash pi/tests/test_model_gateway_setup_guard.sh`.

When a gateway update needs a newer proxy (or `doctor` reports the proxy failing),
rebuild:

```sh
ssh pi@vanpi.lan /home/pi/scripts/build_claude_code_proxy.sh
```

`--tag vX.Y.Z` pins a release; the default is the latest release, which is what
`setup` would fetch. Revisit this if the proxy project publishes a musl or older-glibc
build.

## Remote Control sessions

`/remote-control` only works when `ANTHROPIC_BASE_URL` is unset or
`https://api.anthropic.com`, which the gateway wiring replaces. `claudepd` and
`claudeprd` in `pi/.bashrc` pass `--settings` with the real host for that one session
(no GPT rows there); `claudep`, `clauder`, and `claudepr` keep the gateway. They mirror
the Mac's `~/.zshrc` functions.
