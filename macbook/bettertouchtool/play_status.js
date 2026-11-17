// Inline BTT content script for Media > PPause, installed by install_media_scrub.py,
// which replaces the quoted placeholder below with the absolute path of
// macbook/scripts/media_scrub.py. It reads the local watcher's cached snapshot (no ssh);
// the CLI prints "Status unavailable" itself when the Pi has no fresh sample.
async function itemScript(itemUUID) {
    const unavailable = '__BTT_PLAY_STATUS_UNAVAILABLE__';
    const cli = "__MEDIA_SCRUB_PATH__";
    const quoted = "'" + cli.replace(/'/g, "'\\''") + "'";
    const command = '/usr/bin/python3 -B ' + quoted + ' status --text --wait 2 2>/dev/null || ' +
        "/usr/bin/printf '%s' " + unavailable;
    let text;
    try {
        const result = await runShellScript({script: command, launchPath: '/bin/zsh',
            parameters: '-c', environmentVariables: ''});
        const status = String(result == null ? '' : result).trim();
        text = status.includes(unavailable) ? 'Status unavailable' : status;
    } catch (_) {
        text = 'Status unavailable';
    }
    return JSON.stringify({BTTMenuItemText: text});
}
