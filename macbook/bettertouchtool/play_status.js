// Inline BTT content script for Media > PPause. Installed by tune_media_status.js.
// The remote idle-player probe writes curl progress/errors to stderr; those are
// diagnostics, not playback status. Suppress them only for this display query.
async function itemScript(itemUUID) {
    const unavailable = '__BTT_PLAY_STATUS_UNAVAILABLE__';
    const command = '/usr/bin/ssh -T -o BatchMode=yes -o ConnectTimeout=2 ' +
        '-o ConnectionAttempts=1 -o ServerAliveInterval=2 -o ServerAliveCountMax=1 ' +
        "pi@vanpi.lan 'play_status 2>/dev/null' 2>/dev/null || " +
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
