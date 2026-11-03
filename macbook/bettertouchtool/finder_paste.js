// Runtime for the Finder paste key (install_finder_paste.py): Cmd-V in Finder saves a
// clipboard image as a PNG in the folder a paste would use, and pastes normally
// otherwise. BTT's JXA engine runs it after text_selection.js and text_menu_paste.js,
// whose Accessibility and Edit-menu helpers it uses. It never writes the clipboard.
ObjC.import('AppKit');

function finderPastePresent(value) {
    // JXA returns nil as a callable wrapper rather than null.
    return Boolean(value) && !(typeof value.isNil === 'function' && value.isNil());
}

function finderPasteTypes(board) {
    // Each item of a multi-item pasteboard lists its own types.
    const types = new Set(ObjC.deepUnwrap(board.types) || []);
    const items = board.pasteboardItems;
    for (let index = 0; finderPastePresent(items) && index < Number(items.count); index++)
        for (const type of ObjC.deepUnwrap(items.objectAtIndex(index).types) || []) types.add(type);
    return types;
}

function finderPasteKind(board) {
    const types = finderPasteTypes(board);
    // Files come first: Finder puts each copied file's icon on the pasteboard as TIFF too.
    if (types.has('public.file-url') || types.has('NSFilenamesPboardType')) return 'files';
    if (types.has('public.png') || board.canReadObjectForClassesOptions($([$.NSImage]), $({}))) return 'image';
    return 'other';
}

function finderPastePNG(board) {
    // A screenshot's own PNG is written byte for byte; anything else NSImage reads
    // (TIFF, JPEG, HEIC, PDF) is converted.
    const png = board.dataForType('public.png');
    if (finderPastePresent(png) && Number(png.length) > 0) return png;
    const images = board.readObjectsForClassesOptions($([$.NSImage]), $({}));
    if (!finderPastePresent(images) || !Number(images.count)) throw new Error('The clipboard image could not be read.');
    const bitmap = $.NSBitmapImageRep.imageRepWithData(images.objectAtIndex(0).TIFFRepresentation);
    const data = finderPastePresent(bitmap) && bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
    if (!finderPastePresent(data) || !Number(data.length)) throw new Error('The clipboard image could not be converted to PNG.');
    return data;
}

function finderPasteName(date, copy) {
    const pad = value => String(value).padStart(2, '0');
    const stamp = date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' at '
        + pad(date.getHours()) + '.' + pad(date.getMinutes()) + '.' + pad(date.getSeconds());
    return 'Screenshot ' + stamp + (copy > 1 ? ' ' + copy : '') + '.png';
}

function finderPasteExists(path) {
    // Not fileExistsAtPath: that follows a symlink and misses a dangling one.
    return finderPastePresent($.NSFileManager.defaultManager.attributesOfItemAtPathError(path, null));
}

function saveClipboardImage(board, folder, date) {
    const manager = $.NSFileManager.defaultManager, directory = Ref();
    if (!manager.fileExistsAtPathIsDirectory(folder, directory) || !directory[0])
        throw new Error('The paste folder does not exist: ' + folder);
    const data = finderPastePNG(board);
    // Written under a hidden name first, so Finder never shows a half-written image.
    const temporary = folder + '/.finder-paste-' + ObjC.unwrap($.NSUUID.UUID.UUIDString) + '.png';
    if (!data.writeToFileOptionsError(temporary, $.NSDataWritingWithoutOverwriting, null))
        throw new Error('Could not write the image into ' + folder);
    try {
        for (let copy = 1; copy <= 999; copy++) {
            const path = folder + '/' + finderPasteName(date, copy);
            if (finderPasteExists(path)) continue;
            // moveItem refuses an existing destination, so a file that appears meanwhile is never replaced.
            if (manager.moveItemAtPathToPathError(temporary, path, null)) return path;
            if (!finderPasteExists(path)) throw new Error('Could not name the image in ' + folder);
        }
        throw new Error('No free screenshot name is left in ' + folder);
    } finally {
        if (finderPasteExists(temporary)) manager.removeItemAtPathError(temporary, null);
    }
}

function finderFolderPath(url) {
    const parsed = $.NSURL.URLWithString(url);
    return finderPastePresent(parsed) && finderPastePresent(parsed.path) ? ObjC.unwrap(parsed.path) : '';
}

function finderInsertionFolder() {
    // The folder Finder's own New Folder and Paste would use: the front window's, or the
    // Desktop with no window open. A virtual window (Recents, search results) has none,
    // and Finder then reports a nameless alias file.
    const finder = Application('Finder');
    let path = '';
    try {
        path = finderFolderPath(finder.insertionLocation().url());
    } catch (error) {
        // Handled below.
    }
    if (!path && !finder.windows.length) path = finderFolderPath(finder.desktop.url());
    if (!path) throw new Error('This Finder window has no folder to paste into.');
    return path;
}

function finderApplication() {
    if (!$.AXIsProcessTrusted()) throw new Error('BetterTouchTool needs Accessibility access.');
    const application = selectionAttribute($.AXUIElementCreateSystemWide(), 'AXFocusedApplication');
    if (selectionApplicationBundle(application) !== 'com.apple.finder') throw new Error('Finder is no longer frontmost.');
    return application;
}

function finderPastesImageItself(application) {
    // Text fields take the image's text, and the Get Info icon well sets a custom icon.
    const element = selectionAttribute(application, 'AXFocusedUIElement', {optional: true});
    const settable = Ref();
    if (element && $.AXUIElementIsAttributeSettable(element, $('AXSelectedText'), settable) === 0 && settable[0]) return true;
    const windows = Application('Finder').windows;
    // JXA spells some class names as the dictionary does ("desktop-object"), others camel-cased.
    return windows.length > 0 && String(windows[0].class()).replace(/[\s-]/g, '').toLowerCase() === 'informationwindow';
}

function pressFinderPaste(options, application) {
    // A named pasteboard is only inspected: Finder's Paste would read the general one.
    if (options.pasteboard) return 'normal paste';
    const item = findEditMenuItem(application || finderApplication(), selectionAttribute, 'v');
    // Finder disables Paste when it has nothing to paste; a disabled item is not an error.
    if (ObjC.unwrap(selectionAttribute(item, 'AXEnabled')) !== true) return 'nothing to paste';
    if ($.AXUIElementPerformAction(item, $('AXPress')) !== 0) throw new Error('Finder refused its Paste command.');
    return 'pasted';
}

function selectInFinder(path) {
    // Finder notices a new file a moment after it is written.
    for (let attempt = 0; attempt < 10; attempt++) {
        try {
            Application('Finder').select(Path(path));
            return true;
        } catch (error) {
            delay(0.1);
        }
    }
    return false;
}

function finderPaste(options) {
    const board = options.pasteboard ? $.NSPasteboard.pasteboardWithName(options.pasteboard)
        : $.NSPasteboard.generalPasteboard;
    if (finderPasteKind(board) !== 'image') return pressFinderPaste(options);
    // A given destination is a plain save: no Finder state is read or changed.
    const application = options.destination ? null : finderApplication();
    if (application && finderPastesImageItself(application)) return pressFinderPaste(options, application);
    const path = saveClipboardImage(board, options.destination || finderInsertionFolder(), new Date());
    const name = path.slice(path.lastIndexOf('/') + 1);
    if (!application) return 'saved ' + name;
    return selectInFinder(path) ? 'saved ' + name : 'saved ' + name + ' (not selected)';
}

function runFinderPaste(options) {
    let outcome;
    try {
        outcome = {ok: true, result: finderPaste(options || {})};
    } catch (error) {
        outcome = {ok: false, error: String(error.message || error)};
    }
    return JSON.stringify(outcome);
}
