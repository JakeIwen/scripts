// BTT's bundled CLI uses its user-private local socket, without Apple Events.
ObjC.import('Foundation');
var BTTSocket = (function () {
    function call(command, parameters) {
        const task = $.NSTask.alloc.init;
        task.launchPath = '/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli';
        task.arguments = [command].concat(Object.keys(parameters || {}).map(
            key => key + '=' + String(parameters[key])));
        const pipe = $.NSPipe.pipe;
        task.standardOutput = pipe;
        task.launch;
        const data = pipe.fileHandleForReading.readDataToEndOfFile;
        task.waitUntilExit;
        if (task.terminationStatus !== 0) throw new Error('BTT socket command failed: ' + command);
        const result = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding));
        // Callers validate JSON reads and mutation readback; response prose is not a success contract.
        return result;
    }
    return {call};
})();
