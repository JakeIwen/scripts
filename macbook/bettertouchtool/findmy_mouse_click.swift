import CoreGraphics
import Foundation

guard CommandLine.arguments.count == 3,
      let x = Double(CommandLine.arguments[1]),
      let y = Double(CommandLine.arguments[2]) else {
    FileHandle.standardError.write(Data("usage: findmy_mouse_click <x> <y>\n".utf8))
    exit(64)
}

let point = CGPoint(x: x, y: y)
let source = CGEventSource(stateID: .hidSystemState)

guard let mouseDown = CGEvent(
    mouseEventSource: source,
    mouseType: .leftMouseDown,
    mouseCursorPosition: point,
    mouseButton: .left
), let mouseUp = CGEvent(
    mouseEventSource: source,
    mouseType: .leftMouseUp,
    mouseCursorPosition: point,
    mouseButton: .left
) else {
    FileHandle.standardError.write(Data("could not create CoreGraphics mouse events\n".utf8))
    exit(70)
}

mouseDown.post(tap: .cghidEventTap)
usleep(50_000)
mouseUp.post(tap: .cghidEventTap)
