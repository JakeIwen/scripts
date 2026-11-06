// An in-memory NSPasteboard and the Foundation shims its callers use, for the
// clipboard code in text_menu_paste.js (Paste's temporary clipboard and the
// editing keys' Cmd-C). Items keep every type's data; `prepared` lists the
// options of each prepareForNewContentsWithOptions.
const array = values => ({count:values.length, objectAtIndex:i => values[i]});
const mutableArray = () => {
    const values = [];
    return {values, get count() { return values.length; }, objectAtIndex:i => values[i], addObject:value => values.push(value)};
};
const data = value => ({value, length:value.length, get copy() { return data(value); }});
class Item {
    constructor(values = {}) { this.values = new Map(Object.entries(values).map(([k,v]) => [k,data(v)])); }
    get types() { return array([...this.values.keys()]); }
    dataForType(type) { return this.values.get(type); }
    stringForType(type) { return this.dataForType(type)?.value; }
    setDataForType(value, type) { this.values.set(type,value); return true; }
    setStringForType(value, type) { return this.setDataForType(data(value),type); }
}

const PLAIN_TEXT = 'public.utf8-plain-text';

// `items` are type → string maps; `native` is merged into a test's `$`, and
// `contents()` reads the items back as type → string maps.
function pasteboardFake({items = [{[PLAIN_TEXT]:'original','public.rtf':'rich original'}, {'custom/type':'second'}],
    changeCount = 10} = {}) {
    const board = {
        items:items.map(values => new Item(values)), changeCount, prepared:[],
        get pasteboardItems() { return array(this.items); },
        get text() { return this.stringForType(PLAIN_TEXT); },
        stringForType(type) { return this.items.find(item => item.dataForType(type))?.stringForType(type); },
        get clearContents() { this.items = []; return ++this.changeCount; },
        prepareForNewContentsWithOptions(options) { this.prepared.push(options); return this.clearContents; },
        writeObjects(items) {
            // Native semantics (measured): writeObjects appends and never bumps changeCount;
            // only clearContents / prepareForNewContents start a new generation.
            this.items.push(...(Array.isArray(items) ? items : Array.from({length:Number(items.count)},(_,i) => items.objectAtIndex(i))));
            return true;
        }
    };
    const native = {
        NSPasteboardItem:{alloc:{get init() { return new Item(); }}},
        NSArray:{arrayWithObject:item => array([item])}, NSMutableArray:{get array() { return mutableArray(); }},
        NSPasteboardTypeString:PLAIN_TEXT, NSPasteboardContentsCurrentHostOnly:1,
        NSPasteboard:{generalPasteboard:board}
    };
    const contents = () => Array.from(board.items, item => Object.fromEntries([...item.values].map(([k,v]) => [k,v.value])));
    return {board, native, contents};
}

module.exports = {PLAIN_TEXT, array, Item, pasteboardFake};
