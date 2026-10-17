const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const array = values => ({count:values.length, objectAtIndex:i => values[i]});
const mutableArray = () => {
    const values = [];
    return {get count() { return values.length; }, objectAtIndex:i => values[i], addObject:value => values.push(value)};
};
const data = value => ({value, length:value.length, get copy() { return data(value); }});
class Item {
    constructor(values = {}) { this.values = new Map(Object.entries(values).map(([k,v]) => [k,data(v)])); }
    get types() { return array([...this.values.keys()]); }
    dataForType(type) { return this.values.get(type); }
    setDataForType(value, type) { this.values.set(type,value); return true; }
    setStringForType(value, type) { return this.setDataForType(data(value),type); }
}

function fixture() {
    const board = {
        items:[new Item({'public.utf8-plain-text':'original','public.rtf':'rich original'}), new Item({'custom/type':'second'})],
        changeCount:10,
        get pasteboardItems() { return array(this.items); },
        stringForType(type) { return this.items[0]?.dataForType(type)?.value; },
        get clearContents() { this.items = []; return ++this.changeCount; },
        prepareForNewContentsWithOptions(options) { assert.equal(options,1); return this.clearContents; },
        writeObjects(items) {
            this.items = Array.isArray(items) ? items : Array.from({length:Number(items.count)},(_,i) => items.objectAtIndex(i));
            this.changeCount++;
            return true;
        }
    };
    const native = value => value;
    Object.assign(native, {
        NSPasteboardItem:{alloc:{get init() { return new Item(); }}},
        NSArray:{arrayWithObject:item => array([item])}, NSMutableArray:{get array() { return mutableArray(); }},
        NSPasteboardTypeString:'public.utf8-plain-text', NSPasteboardContentsCurrentHostOnly:1,
        NSPasteboard:{generalPasteboard:board}, CFEqual:(a,b) => a === b
    });
    const context = vm.createContext({$:native, ObjC:{import() {},unwrap:x => x}, delay() {}});
    vm.runInContext(fs.readFileSync(path.join(__dirname,'../text_menu_paste.js'),'utf8'),context);
    const contents = () => Array.from(board.items, item => Object.fromEntries([...item.values].map(([k,v]) => [k,v.value])));
    return {board, native, context, contents};
}

test('temporary paste data is marked private and all clipboard items/formats are restored', () => {
    const {board,context,contents} = fixture();
    const original = contents();
    context.withTemporaryClipboard(board,'TRANSFORMED',owns => {
        assert.ok(owns());
        assert.deepEqual(contents(), [{'public.utf8-plain-text':'TRANSFORMED',
            'org.nspasteboard.TransientType':'1','org.nspasteboard.ConcealedType':'1'}]);
    });
    assert.deepEqual(contents(),original);
});

test('a refused paste restores the clipboard without retrying', () => {
    const {board,context,contents} = fixture();
    const original = contents();
    assert.throws(() => context.withTemporaryClipboard(board,'TRANSFORMED',() => {throw Error('refused');}), /refused/);
    assert.deepEqual(contents(),original);
});

test('a newer user copy is preserved instead of overwritten with the old clipboard', () => {
    const {board,context,contents} = fixture();
    context.withTemporaryClipboard(board,'TRANSFORMED',owns => {
        board.clearContents;
        board.writeObjects([new Item({'public.utf8-plain-text':'new user copy'})]);
        assert.equal(owns(),false);
    });
    assert.deepEqual(contents(),[{'public.utf8-plain-text':'new user copy'}]);
});

test('an unreadable clipboard format stops before clearing any clipboard data', () => {
    const {board,context} = fixture();
    board.items[0].dataForType = () => undefined;
    assert.throws(() => context.withTemporaryClipboard(board,'TRANSFORMED',() => assert.fail('paste called')), /preserve/);
    assert.equal(board.changeCount,10);
});

test('a successful-looking clipboard write cannot paste missing or stale text', () => {
    const {board,context,contents} = fixture();
    const original = contents(), write = board.writeObjects;
    let first = true;
    board.writeObjects = function(items) {
        if (first) { first = false; return true; }
        return write.call(this,items);
    };
    assert.throws(() => context.withTemporaryClipboard(board,'TRANSFORMED',() => assert.fail('paste called')), /not verified/);
    assert.deepEqual(contents(),original);
});

function pasteFixture(refuse = false, {text = 'mixed suffix', location = 0, length = 5} = {}) {
    const base = fixture(), system = {}, application = {}, editor = {}, menu = {}, paste = {};
    const state = {value:text,range:{location,length},pending:false,presses:0};
    const selectedText = () => state.value.slice(state.range.location,state.range.location+state.range.length);
    const attribute = (element,name) => {
        if (element === system) return name === 'AXFocusedApplication' ? application : editor;
        if (element === application) return menu;
        if (element === menu) return name === 'AXChildren' ? array([paste]) : 'AXMenu';
        if (element === paste) return {AXRole:'AXMenuItem',AXMenuItemCmdChar:'v',AXMenuItemCmdModifiers:0,AXEnabled:true}[name] ?? null;
        if (element === editor) return {AXValue:state.value,AXSelectedTextRange:state.range,
            AXSelectedText:selectedText()}[name];
        throw Error('Unexpected target');
    };
    base.native.AXUIElementPerformAction = (element,action) => {
        assert.equal(element,paste); assert.equal(action,'AXPress'); state.presses++;
        const replacement = base.board.items[0].dataForType('public.utf8-plain-text').value;
        state.expected = state.value.slice(0,state.range.location) + replacement
            + state.value.slice(state.range.location+state.range.length);
        state.pending = !refuse; return refuse ? -1 : 0;
    };
    base.native.AXUIElementSetAttributeValue = (element,name,value) => {
        assert.equal(element,editor); assert.equal(name,'AXSelectedTextRange');
        assert.equal(state.value,state.expected); state.range = value; return 0;
    };
    base.context.delay = () => { if (state.pending) { state.value = state.expected; state.pending = false; } };
    const helpers = {attribute, offsets:range => range,
        range:(original,replacement) => ({location:original.location,length:replacement.length})};
    const run = (replacement = 'MIXED') => {
        const selection = {element:editor,range:state.range,text:selectedText()};
        base.context.pasteSelectionUsingMenu(system,application,selection,replacement,helpers);
    };
    return {...base,state,selectedText,run};
}

test('upper then lower preserves a URL and its middle selection without refocusing', () => {
    // Preserve the reported URL shape/offset without retaining a real conversation ID.
    const original = 'claude.ai/chat/45fae000-0000-0000-0000-000000000000';
    const {run,state,selectedText,contents} = pasteFixture(false,{text:original,location:17,length:3});
    const clipboard = contents();
    const transform = require('../lib/text_transforms.js').transform;
    assert.equal(selectedText(),'fae');
    run(transform('upper',selectedText(),{}));
    assert.equal(state.value,original.replace('fae','FAE'));
    assert.equal(selectedText(),'FAE');
    assert.deepEqual(state.range,{location:17,length:3});
    run(transform('lower',selectedText(),{}));
    assert.equal(state.value,original);
    assert.equal(selectedText(),'fae');
    assert.deepEqual(state.range,{location:17,length:3});
    assert.equal(state.presses,2);
    assert.deepEqual(contents(),clipboard);
});

test('native Paste runs once, waits for the draft update, restores clipboard and reselects', () => {
    const {run,state,contents} = pasteFixture();
    const original = contents();
    run();
    assert.equal(state.presses,1);
    assert.equal(state.value,'MIXED suffix');
    assert.deepEqual(contents(),original);
});

test('a native menu refusal cannot lead to selection writes or a second paste', () => {
    const {run,state,contents} = pasteFixture(true);
    const original = contents();
    assert.throws(run,/refused its Paste command/);
    assert.equal(state.presses,1);
    assert.equal(state.value,'mixed suffix');
    assert.deepEqual(contents(),original);
});

test('an unconfirmed edit is not reported as success or pasted again', () => {
    const {run,state,contents,context} = pasteFixture();
    const original = contents();
    context.delay = () => {};
    assert.throws(run,/did not confirm/);
    assert.equal(state.presses,1);
    assert.deepEqual(contents(),original);
});

test('native Cocoa pasteboard arrays preserve text, rich data and privacy tags',
    {skip:process.env.BTT_LIVE_TESTS !== '1'}, () => {
        const source = path.resolve(__dirname,'../text_menu_paste.js');
        const probe = `
(function () {
    const board = $.NSPasteboard.pasteboardWithUniqueName;
    try {
        board.clearContents;
        const item = $.NSPasteboardItem.alloc.init;
        item.setStringForType('original', $.NSPasteboardTypeString);
        item.setStringForType('<b>original</b>', 'public.html');
        board.writeObjects($.NSArray.arrayWithObject(item));
        let temporaryText = false, privacyTags = false;
        withTemporaryClipboard(board, 'TRANSFORMED', function (owns) {
            temporaryText = owns() && ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)) === 'TRANSFORMED';
            privacyTags = ObjC.unwrap(board.stringForType('org.nspasteboard.TransientType')) === '1'
                && ObjC.unwrap(board.stringForType('org.nspasteboard.ConcealedType')) === '1';
        });
        return JSON.stringify({temporaryText, privacyTags,
            plainRestored:ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)) === 'original',
            richRestored:ObjC.unwrap(board.stringForType('public.html')) === '<b>original</b>'});
    } finally {
        board.releaseGlobally;
    }
})();`;
        const action = {BTTPredefinedActionType:281, BTTAdditionalActionData:{BTTScriptType:3,
            BTTScriptFunctionToCall:'probePasteboard', BTTActionJSRunInSeparateContext:true,
            BTTAppleScriptString:`async function probePasteboard() {
    const source = await readFile(${JSON.stringify(source)});
    return await runJXA(source + ${JSON.stringify(probe)});
}`}};
        const output = execFileSync('/Applications/BetterTouchTool.app/Contents/SharedSupport/bin/bttcli',
            ['trigger_action','json='+JSON.stringify(action)], {encoding:'utf8',timeout:15000});
        assert.deepEqual(JSON.parse(output), {temporaryText:true,privacyTags:true,plainRestored:true,richRestored:true});
    });
