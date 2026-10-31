import AppKit
import Darwin
import Foundation

func usageError() -> Never {
    FileHandle.standardError.write(Data("Usage: MeteredWiFi [--request-authorization] [--output PATH]\n".utf8))
    exit(64)
}

var requestAuthorization = false
var outputPath: String?
var index = 1
while index < CommandLine.arguments.count {
    switch CommandLine.arguments[index] {
    case "--request-authorization" where !requestAuthorization:
        requestAuthorization = true
    case "--output" where outputPath == nil:
        index += 1
        guard index < CommandLine.arguments.count else { usageError() }
        outputPath = CommandLine.arguments[index]
    default:
        usageError()
    }
    index += 1
}

// Killing a timed-out `open -W` does not kill its app; bound this process even if an API blocks.
alarm(requestAuthorization ? 125 : 8)
let app = NSApplication.shared
let probe = WiFiProbe(outputPath: outputPath, requestAuthorization: requestAuthorization)
app.setActivationPolicy(.accessory)
app.delegate = probe
app.run()
