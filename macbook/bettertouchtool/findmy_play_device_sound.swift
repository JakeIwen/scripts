import AppKit
import ApplicationServices
import Foundation

private let findMyBundleID = "com.apple.findmy"
private let pollIntervalMicroseconds: useconds_t = 20_000

private func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
    exit(code)
}

private func waitUntil(
    timeout: TimeInterval,
    condition: () -> Bool
) -> Bool {
    let deadline = Date().addingTimeInterval(timeout)
    repeat {
        if condition() {
            return true
        }
        usleep(pollIntervalMicroseconds)
    } while Date() < deadline
    return condition()
}

private func attribute(
    _ element: AXUIElement,
    _ name: CFString
) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else {
        return nil
    }
    return value
}

private func stringAttribute(
    _ element: AXUIElement,
    _ name: CFString
) -> String? {
    attribute(element, name) as? String
}

private func elementsAttribute(
    _ element: AXUIElement,
    _ name: CFString
) -> [AXUIElement] {
    attribute(element, name) as? [AXUIElement] ?? []
}

private func pointAttribute(
    _ element: AXUIElement,
    _ name: CFString
) -> CGPoint? {
    guard let rawValue = attribute(element, name),
          CFGetTypeID(rawValue) == AXValueGetTypeID() else {
        return nil
    }

    let value = unsafeBitCast(rawValue, to: AXValue.self)
    guard AXValueGetType(value) == .cgPoint else {
        return nil
    }

    var point = CGPoint.zero
    guard AXValueGetValue(value, .cgPoint, &point) else {
        return nil
    }
    return point
}

private func sizeAttribute(
    _ element: AXUIElement,
    _ name: CFString
) -> CGSize? {
    guard let rawValue = attribute(element, name),
          CFGetTypeID(rawValue) == AXValueGetTypeID() else {
        return nil
    }

    let value = unsafeBitCast(rawValue, to: AXValue.self)
    guard AXValueGetType(value) == .cgSize else {
        return nil
    }

    var size = CGSize.zero
    guard AXValueGetValue(value, .cgSize, &size) else {
        return nil
    }
    return size
}

private func frame(of element: AXUIElement) -> CGRect? {
    guard let origin = pointAttribute(element, kAXPositionAttribute as CFString),
          let size = sizeAttribute(element, kAXSizeAttribute as CFString) else {
        return nil
    }
    return CGRect(origin: origin, size: size)
}

private func descendants(
    of roots: [AXUIElement],
    maximumDepth: Int = 24
) -> [AXUIElement] {
    var result: [AXUIElement] = []
    var queue = roots.map { ($0, 0) }
    var seen: Set<CFHashCode> = []
    var index = 0

    while index < queue.count {
        let (element, depth) = queue[index]
        index += 1

        let elementHash = CFHash(element)
        guard seen.insert(elementHash).inserted else {
            continue
        }
        result.append(element)

        guard depth < maximumDepth else {
            continue
        }

        let childAttributes: [CFString] = [
            kAXChildrenAttribute as CFString,
            kAXVisibleChildrenAttribute as CFString,
            kAXRowsAttribute as CFString,
            kAXVisibleRowsAttribute as CFString,
            kAXContentsAttribute as CFString,
        ]

        for childAttribute in childAttributes {
            for child in elementsAttribute(element, childAttribute) {
                queue.append((child, depth + 1))
            }
        }
    }

    return result
}

private func displayedStrings(of element: AXUIElement) -> [String] {
    [
        stringAttribute(element, kAXTitleAttribute as CFString),
        stringAttribute(element, kAXDescriptionAttribute as CFString),
        stringAttribute(element, kAXValueAttribute as CFString),
    ].compactMap { $0 }
}

private func postCommand2() {
    let source = CGEventSource(stateID: .hidSystemState)
    guard let keyDown = CGEvent(
        keyboardEventSource: source,
        virtualKey: 19,
        keyDown: true
    ), let keyUp = CGEvent(
        keyboardEventSource: source,
        virtualKey: 19,
        keyDown: false
    ) else {
        fail("Could not create Command-2 keyboard events.")
    }

    keyDown.flags = .maskCommand
    keyUp.flags = .maskCommand
    keyDown.post(tap: .cghidEventTap)
    keyUp.post(tap: .cghidEventTap)
}

private func postRightClick(at point: CGPoint) {
    let source = CGEventSource(stateID: .hidSystemState)
    guard let mouseDown = CGEvent(
        mouseEventSource: source,
        mouseType: .rightMouseDown,
        mouseCursorPosition: point,
        mouseButton: .right
    ), let mouseUp = CGEvent(
        mouseEventSource: source,
        mouseType: .rightMouseUp,
        mouseCursorPosition: point,
        mouseButton: .right
    ) else {
        fail("Could not create the right-click mouse events.")
    }

    mouseDown.post(tap: .cghidEventTap)
    mouseUp.post(tap: .cghidEventTap)
}

private func postLeftClick(at point: CGPoint) {
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
        fail("Could not create the left-click mouse events.")
    }

    mouseDown.post(tap: .cghidEventTap)
    mouseUp.post(tap: .cghidEventTap)
}

guard CommandLine.arguments.count == 2 else {
    fail("usage: findmy_play_device_sound <exact device title>", code: 64)
}

let targetTitle = CommandLine.arguments[1]
var findMyApplication: NSRunningApplication?

guard waitUntil(timeout: 5, condition: {
    findMyApplication = NSRunningApplication
        .runningApplications(withBundleIdentifier: findMyBundleID)
        .first
    return findMyApplication != nil
}), let findMyApplication else {
    fail("Find My did not open.")
}

findMyApplication.activate(options: [.activateAllWindows])

guard waitUntil(timeout: 3, condition: {
    NSWorkspace.shared.frontmostApplication?.processIdentifier ==
        findMyApplication.processIdentifier
}) else {
    fail("Find My did not become frontmost.")
}

let findMyAXApplication = AXUIElementCreateApplication(
    findMyApplication.processIdentifier
)

guard waitUntil(timeout: 5, condition: {
    !elementsAttribute(
        findMyAXApplication,
        kAXWindowsAttribute as CFString
    ).isEmpty
}) else {
    fail("Find My did not expose a window.")
}

postCommand2()

var targetElement: AXUIElement?
guard waitUntil(timeout: 5, condition: {
    let windows = elementsAttribute(
        findMyAXApplication,
        kAXWindowsAttribute as CFString
    )
    guard let mainWindow = windows.first,
          let windowFrame = frame(of: mainWindow) else {
        return false
    }

    let sidebarRightEdge = windowFrame.minX + min(430, windowFrame.width * 0.45)
    let matches = descendants(of: [mainWindow]).filter { element in
        guard stringAttribute(
            element,
            kAXRoleAttribute as CFString
        ) == kAXStaticTextRole as String,
        displayedStrings(of: element).contains(targetTitle),
        let elementFrame = frame(of: element) else {
            return false
        }
        return elementFrame.midX < sidebarRightEdge
    }

    guard matches.count == 1 else {
        return false
    }
    targetElement = matches[0]
    return true
}), let targetElement, let targetFrame = frame(of: targetElement) else {
    fail("Could not uniquely locate device “\(targetTitle)” in the Devices list.")
}

postRightClick(at: CGPoint(x: targetFrame.midX, y: targetFrame.midY))

var playSoundMenuItem: AXUIElement?
guard waitUntil(timeout: 3, condition: {
    let windows = elementsAttribute(
        findMyAXApplication,
        kAXWindowsAttribute as CFString
    )
    let matches = descendants(of: windows).filter { element in
        guard stringAttribute(
            element,
            kAXRoleAttribute as CFString
        ) == kAXMenuItemRole as String else {
            return false
        }
        let title = stringAttribute(element, kAXTitleAttribute as CFString)
        let description = stringAttribute(
            element,
            kAXDescriptionAttribute as CFString
        )
        return title == "Play Sound" || description == "Play Sound"
    }

    guard matches.count == 1 else {
        return false
    }
    playSoundMenuItem = matches[0]
    return true
}), let playSoundMenuItem else {
    fail("The context menu did not expose one Play Sound item.")
}

if AXUIElementPerformAction(
    playSoundMenuItem,
    kAXPressAction as CFString
) != .success {
    guard let menuItemFrame = frame(of: playSoundMenuItem) else {
        fail("The Play Sound menu item could not be activated.")
    }
    postLeftClick(
        at: CGPoint(x: menuItemFrame.midX, y: menuItemFrame.midY)
    )
}

print("Requested Play Sound for device “\(targetTitle)”.")
