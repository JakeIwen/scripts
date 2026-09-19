#!/usr/bin/env swift
// Native drop window + CLI. Build with build_strip_metadata_app.py.
import AppKit
import ImageIO
import UniformTypeIdentifiers
import Darwin

struct StripError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

func fail(_ message: String) -> StripError { StripError(message: message) }

// Rebuild from rendered pixels, never copy image properties or embedded previews.
// Only animation timing/loop counts are carried over, via an explicit allowlist.
func cleanFrame(_ source: CGImageSource, _ index: Int) throws -> CGImage {
    let props = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [String: Any] ?? [:]
    guard let width = props[kCGImagePropertyPixelWidth as String] as? Int,
          let height = props[kCGImagePropertyPixelHeight as String] as? Int,
          width > 0, height > 0, width <= 100_000, height <= 100_000,
          Int64(width) * Int64(height) <= 150_000_000 else {
        throw fail("Invalid image dimensions or image exceeds 150 megapixels.")
    }
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: max(width, height),
        kCGImageSourceShouldCacheImmediately: true,
    ]
    guard let decoded = CGImageSourceCreateThumbnailAtIndex(source, index, options as CFDictionary),
          let space = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil, width: decoded.width, height: decoded.height,
                                  bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        throw fail("macOS could not decode this image.")
    }
    context.draw(decoded, in: CGRect(x: 0, y: 0, width: decoded.width, height: decoded.height))
    guard let clean = context.makeImage() else { throw fail("Could not render image.") }
    return clean
}

func animationProperties(_ source: [String: Any], global: Bool) -> [String: Any] {
    var result: [String: Any] = [:]
    for (group, keys) in [
        (kCGImagePropertyGIFDictionary as String, global ? ["LoopCount"] : ["DelayTime", "UnclampedDelayTime"]),
        (kCGImagePropertyPNGDictionary as String, global ? ["LoopCount"] : ["DelayTime", "UnclampedDelayTime"]),
    ] {
        if let values = source[group] as? [String: Any] {
            var clean: [String: Any] = [:]
            for key in keys {
                if let n = values[key] as? NSNumber, n.doubleValue.isFinite, n.doubleValue >= 0 {
                    clean[key] = n
                }
            }
            if !clean.isEmpty { result[group] = clean }
        }
    }
    return result
}

func stripImage(_ input: URL) throws -> URL {
    let file = input.standardizedFileURL
    let attrs = try FileManager.default.attributesOfItem(atPath: file.path)
    guard attrs[.type] as? FileAttributeType == .typeRegular else {
        throw fail("Choose a regular image file, not a folder or symbolic link.")
    }
    guard let source = CGImageSourceCreateWithURL(file as CFURL, nil),
          let sourceType = CGImageSourceGetType(source) else {
        throw fail("This image format cannot be read by macOS.")
    }
    let count = CGImageSourceGetCount(source)
    guard count > 0 else { throw fail("Image has no frames.") }
    let writable = CGImageDestinationCopyTypeIdentifiers() as! [String]
    let originalType = sourceType as String
    let isRaw = UTType(originalType)?.conforms(to: .rawImage) ?? false
    let sameType = writable.contains(originalType) && !isRaw
    let outputType = sameType ? originalType : UTType.png.identifier
    // Never silently discard animation/pages for formats without an encoder.
    guard sameType || count == 1 else {
        throw fail("macOS cannot write this multi-frame format without losing frames.")
    }
    let fileExtension = sameType && !file.pathExtension.isEmpty
        ? file.pathExtension : (UTType(outputType)?.preferredFilenameExtension ?? "png")
    let folder = file.deletingLastPathComponent()
    let stem = file.deletingPathExtension().lastPathComponent
    let temporary = folder.appendingPathComponent(".strip-\(UUID().uuidString).\(fileExtension)")
    // Reserve a private temporary file; neither source xattrs nor filesystem dates are copied.
    let fd = Darwin.open(temporary.path, O_CREAT | O_EXCL | O_WRONLY, S_IRUSR | S_IWUSR)
    guard fd >= 0 else { throw fail("Cannot create a new image in this folder: \(String(cString: strerror(errno)))") }
    Darwin.close(fd)
    defer { try? FileManager.default.removeItem(at: temporary) }
    guard let destination = CGImageDestinationCreateWithURL(temporary as CFURL, outputType as CFString, count, nil) else {
        throw fail("macOS has no encoder for this image format.")
    }
    let global = CGImageSourceCopyProperties(source, nil) as? [String: Any] ?? [:]
    CGImageDestinationSetProperties(destination, animationProperties(global, global: true) as CFDictionary)
    for index in 0..<count {
        try autoreleasepool {
            let frame = try cleanFrame(source, index)
            let original = CGImageSourceCopyPropertiesAtIndex(source, index, nil) as? [String: Any] ?? [:]
            var properties = animationProperties(original, global: false)
            properties[kCGImageDestinationLossyCompressionQuality as String] = 1.0
            CGImageDestinationAddImage(destination, frame, properties as CFDictionary)
        }
    }
    guard CGImageDestinationFinalize(destination),
          let check = CGImageSourceCreateWithURL(temporary as CFURL, nil),
          CGImageSourceGetCount(check) == count else {
        throw fail("Could not finish the output image; no copy was published.")
    }
    for index in 0..<count {
        guard CGImageSourceCreateImageAtIndex(check, index, nil) != nil else {
            throw fail("Output validation failed; no copy was published.")
        }
    }
    // Atomic publication with EXCL: a concurrent run or existing file cannot be overwritten.
    for number in 1...10_000 {
        let suffix = number == 1 ? "_stripped" : "_stripped_\(number)"
        let output = folder.appendingPathComponent("\(stem)\(suffix).\(fileExtension)")
        if renamex_np(temporary.path, output.path, UInt32(RENAME_EXCL)) == 0 { return output }
        guard errno == EEXIST else { throw fail("Cannot save output: \(String(cString: strerror(errno)))") }
    }
    throw fail("Too many existing stripped copies.")
}

final class DropView: NSView {
    var receive: (([URL]) -> Void)?
    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
    }
    required init?(coder: NSCoder) { fatalError() }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { .copy }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = sender.draggingPasteboard.readObjects(forClasses: [NSURL.self],
                    options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        guard !urls.isEmpty else { return false }
        receive?(urls)
        return true
    }
}

final class StripApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    var window: NSWindow!
    var log: NSTextView!
    var choose: NSButton!
    var reveal: NSButton!
    var busy = false
    var outputs: [URL] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Strip Metadata", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu
        menu.addItem(item)
        NSApp.mainMenu = menu
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 590, height: 390),
                          styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        window.title = "Strip Metadata"
        window.delegate = self
        let view = DropView(frame: window.contentView!.bounds)
        view.receive = { [weak self] urls in self?.process(urls) }
        window.contentView = view
        let title = NSTextField(labelWithString: "Drop images here")
        title.font = .systemFont(ofSize: 25, weight: .semibold)
        title.frame = NSRect(x: 24, y: 330, width: 540, height: 36)
        view.addSubview(title)
        let description = NSTextField(wrappingLabelWithString:
            "Creates new _stripped copies in the same folder. Originals stay untouched.\nImages are re-encoded in sRGB; RAW/read-only formats become PNG.")
        description.frame = NSRect(x: 24, y: 275, width: 540, height: 48)
        description.textColor = .secondaryLabelColor
        view.addSubview(description)
        choose = NSButton(title: "Choose Images…", target: self, action: #selector(pick))
        choose.bezelStyle = .rounded
        choose.frame = NSRect(x: 20, y: 225, width: 170, height: 34)
        view.addSubview(choose)
        reveal = NSButton(title: "Show Copies in Finder", target: self, action: #selector(showOutputs))
        reveal.bezelStyle = .rounded
        reveal.frame = NSRect(x: 335, y: 225, width: 230, height: 34)
        reveal.isEnabled = false
        view.addSubview(reveal)
        let scroll = NSScrollView(frame: NSRect(x: 24, y: 24, width: 542, height: 185))
        scroll.hasVerticalScroller = true
        scroll.borderType = .bezelBorder
        log = NSTextView(frame: scroll.bounds)
        log.isEditable = false
        log.isSelectable = true
        log.font = .systemFont(ofSize: 13)
        log.autoresizingMask = [.width]
        log.textContainer?.widthTracksTextView = true
        scroll.documentView = log
        view.addSubview(scroll)
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func windowShouldClose(_ sender: NSWindow) -> Bool { !busy }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        busy ? .terminateCancel : .terminateNow
    }
    @objc func pick() {
        let panel = NSOpenPanel()
        panel.title = "Choose images to strip"
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.beginSheetModal(for: window) { response in
            if response == .OK { self.process(panel.urls) }
        }
    }
    @objc func showOutputs() { NSWorkspace.shared.activateFileViewerSelecting(outputs) }
    func append(_ text: String) {
        log.string += text + "\n"
        log.scrollToEndOfDocument(nil)
    }
    func process(_ urls: [URL]) {
        guard !busy else { append("Please wait for the current batch to finish, then drop again."); return }
        busy = true
        choose.isEnabled = false
        append("Processing \(urls.count) image(s)…")
        DispatchQueue.global(qos: .userInitiated).async {
            for url in urls {
                do {
                    let result = try autoreleasepool { try stripImage(url) }
                    DispatchQueue.main.async {
                        self.outputs.append(result)
                        self.append("Saved: \(result.path)")
                    }
                } catch {
                    let message = error.localizedDescription
                    DispatchQueue.main.async { self.append("Failed: \(url.lastPathComponent) — \(message)") }
                }
            }
            DispatchQueue.main.async {
                self.busy = false
                self.choose.isEnabled = true
                self.reveal.isEnabled = !self.outputs.isEmpty
                self.append("Done. Drop more images or choose another batch.")
            }
        }
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())
if !arguments.isEmpty {
    if arguments == ["--help"] {
        print("Usage: StripMetadata [IMAGE ...]\nWithout arguments, opens a persistent file-picker/drop window.")
    } else {
        var failed = false
        for path in arguments {
            do { print(try stripImage(URL(fileURLWithPath: path)).path) }
            catch { fputs("\(path): \(error.localizedDescription)\n", stderr); failed = true }
        }
        if failed { exit(1) }
    }
} else {
    let app = NSApplication.shared
    app.setActivationPolicy(.regular)
    let delegate = StripApp()
    app.delegate = delegate
    app.run()
}
