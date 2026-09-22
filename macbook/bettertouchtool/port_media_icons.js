// Apply icon/text appearance patches only. Never imports menu or action trees.
ObjC.import('Foundation');
ObjC.import('AppKit');
const ICON_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const STATUS_BUTTON = 'BDFEAEF1-6961-4B05-9A62-E70C54332C4C';
const JOIN_BUTTON = '1CEF0D9C-AB16-48F2-AF26-AE3740217CEA';

function blackRTF(raw) {
    if(!raw)return raw;
    if(typeof raw!=='string' || !raw.trim().startsWith('{\\rtf')) {
        throw new Error('Unsupported rich-text label format; no changes made.');
    }
    const data=$(raw).dataUsingEncoding($.NSUTF8StringEncoding);
    const text=$.NSMutableAttributedString.alloc.initWithRTFDocumentAttributes(data,null);
    if(!text)throw new Error('Cannot decode a main-menu label; no changes made.');
    const length=Number(text.length);
    let alreadyBlack=true;
    for(let i=0;i<length;i++) {
        const color=text.attributeAtIndexEffectiveRange($.NSForegroundColorAttributeName,i,null);
        // JXA represents a nil Objective-C id as a truthy wrapper, so test for
        // the conversion method instead of relying on JavaScript truthiness.
        if(!color || typeof color.colorUsingColorSpace!=='function') {alreadyBlack=false;break;}
        const rgb=color.colorUsingColorSpace($.NSColorSpace.genericRGBColorSpace);
        if(!rgb || Number(rgb.redComponent)!==0 || Number(rgb.greenComponent)!==0 ||
            Number(rgb.blueComponent)!==0 || Number(rgb.alphaComponent)!==1) {alreadyBlack=false;break;}
    }
    if(alreadyBlack)return raw;
    const range=$.NSMakeRange(0,length);
    text.addAttributeValueRange($.NSForegroundColorAttributeName,$.NSColor.blackColor,range);
    const result=text.RTFFromRangeDocumentAttributes(range,$({}));
    if(!result)throw new Error('Cannot encode a main-menu label; no changes made.');
    return ObjC.unwrap($.NSString.alloc.initWithDataEncoding(result,$.NSUTF8StringEncoding));
}
function mainStyleChanges(input,recolor) {
    const icons=new Map(input.icons.map(x=>[x.uuid,x]));
    const seen=new Set(),changes=[];
    for(const item of input.items) {
        if(seen.has(item.uuid) || ![773,774].includes(Number(item.type)))throw new Error('Unexpected main item list.');
        seen.add(item.uuid);
        const config=item.config,patch={};
        for(const suffix of ['', 'Dark']) {
            patch['BTTMenuItemFontColor'+suffix]='0, 0, 0, 255';
            patch['BTTMenuItemFontColorHover'+suffix]='0, 0, 0, 255';
            if(item.uuid===STATUS_BUTTON) {
                // This is an SF Symbol, not emoji/RTF text. Its tint is separate
                // from the status-text color and must be set explicitly.
                patch['BTTMenuItemIconColor1'+suffix]='0, 0, 0, 255';
                patch['BTTMenuItemIconColor1Hover'+suffix]='0, 0, 0, 255';
            }
        }
        for(const key of ['BTTMenuAttributedText','BTTMenuAttributedTextDark','BTTMenuAttributedTextAlt']) {
            if(config[key])patch[key]=recolor(config[key]);
        }
        if(config.BTTMenuAttributedTextAlt) {
            patch.BTTMenuItemFontColorAlt='0, 0, 0, 255';
            patch.BTTMenuItemFontColorHoverAlt='0, 0, 0, 255';
        }
        if(item.uuid===JOIN_BUTTON) {
            // Legacy left=8/right=-9 padding shifted a centered icon sideways.
            patch.BTTMenuItemPaddingLeft=0;
            patch.BTTMenuItemPaddingRight=0;
            for(const suffix of ['', 'Dark']) {
                patch['BTTMenuItemIconPosition'+suffix]=4;
                for(const axis of ['X','Y']) {
                    const key='BTTMenuItemImageOffset'+axis+suffix;
                    if(Number(config[key]||0)!==0)patch[key]=0;
                }
            }
        }
        const icon=icons.get(item.uuid);
        if(icon)Object.assign(patch,icon.patch);
        for(const key of Object.keys(patch))if(config[key]===patch[key])delete patch[key];
        if(Object.keys(patch).length)changes.push({uuid:item.uuid,
            label:icon?icon.label:(item.uuid===JOIN_BUTTON?'Sonos partymode':(config.BTTMenuElementIdentifier||item.uuid)),patch});
    }
    for(const id of icons.keys())if(!seen.has(id))throw new Error('Speaker icon target is not in Media.');
    return changes;
}
function normalizeIconConfig(actual,expected,equivalent) {
    const result=JSON.parse(JSON.stringify(actual));
    for(const suffix of ['', 'Dark']) {
        const type='BTTMenuItemIconType'+suffix,image='BTTMenuItemImage'+suffix;
        const path='BTTMenuItemIconPresetPath'+suffix,tint='BTTMenuItemImageChangeColor'+suffix;
        if(Number(expected[type])===1 && Number(actual[type])===7 && typeof expected[image]==='string' && actual[path]) {
            // A tinted image is an alpha mask. Untinted artwork must match RGBA.
            const maskOnly=Number(expected[tint])===1 && Number(actual[tint])===1;
            if(equivalent(expected[image],actual[path],maskOnly)) {
                result[type]=expected[type];result[image]=expected[image];
                if(Object.prototype.hasOwnProperty.call(expected,path))result[path]=expected[path];
                else delete result[path];
            }
        }
    }
    return result;
}
function normalizeIconTree(actual,expected,equivalent) {
    if(Array.isArray(actual)) {
        const wanted=Array.isArray(expected)?expected:[];
        const byID=new Map(wanted.filter(x=>x&&x.BTTUUID).map(x=>[x.BTTUUID,x]));
        return actual.map((x,i)=>normalizeIconTree(x,x&&x.BTTUUID?byID.get(x.BTTUUID):wanted[i],equivalent));
    }
    if(actual && typeof actual==='object') {
        const result={};
        for(const key of Object.keys(actual)) {
            result[key]=key==='BTTMenuConfig' && expected && expected[key]?
                normalizeIconConfig(actual[key],expected[key],equivalent):
                normalizeIconTree(actual[key],expected&&expected[key],equivalent);
        }
        return result;
    }
    return actual;
}
function nativeIconMatcher(presetPath) {
    const cache=new Map();
    const root=ObjC.unwrap($(presetPath).stringByStandardizingPath.stringByResolvingSymlinksInPath);
    function signature(data,maskOnly) {
        if(!data || typeof data.base64EncodedStringWithOptions!=='function' || Number(data.length)>2097152) {
            throw new Error('Invalid or oversized icon data during verification.');
        }
        const key=(maskOnly?'alpha:':'rgba:')+ObjC.unwrap(data.base64EncodedStringWithOptions(0));
        if(cache.has(key))return cache.get(key);
        const bitmap=$.NSBitmapImageRep.imageRepWithData(data);
        const w=Number(bitmap.pixelsWide),h=Number(bitmap.pixelsHigh);
        if(!Number.isInteger(w)||!Number.isInteger(h)||w<1||h<1||w>512||h>512) {
            throw new Error('Cannot verify icon pixel dimensions.');
        }
        let pixels='';
        for(let y=0;y<h;y++)for(let x=0;x<w;x++) {
            const color=bitmap.colorAtXY(x,y),alpha=Number(color.alphaComponent);
            let channels=[alpha];
            if(!maskOnly) {
                const rgb=color.colorUsingColorSpace($.NSColorSpace.genericRGBColorSpace);
                channels=[Number(rgb.redComponent),Number(rgb.greenComponent),Number(rgb.blueComponent),alpha];
            }
            if(!channels.every(n=>Number.isFinite(n)&&n>=0&&n<=1))throw new Error('Unsupported icon color data.');
            pixels+=String.fromCharCode(...channels.map(n=>Math.round(n*255)));
        }
        const value=w+'x'+h+':'+pixels;cache.set(key,value);return value;
    }
    return (encoded,path,maskOnly)=>{
        const expanded=path.startsWith('BTT_PRESET_PATH/')?root+'/'+path.slice('BTT_PRESET_PATH/'.length):path;
        const resolved=ObjC.unwrap($(expanded).stringByStandardizingPath.stringByResolvingSymlinksInPath);
        if(!resolved.startsWith(root+'/'))throw new Error('Icon path is outside the verified preset bundle.');
        const wanted=$.NSData.alloc.initWithBase64EncodedStringOptions(encoded,0);
        const saved=$.NSData.dataWithContentsOfFile(resolved);
        return signature(wanted,maskOnly)===signature(saved,maskOnly);
    };
}
function iconValidate(changes) {
    for(const change of changes) {
        if(!Object.prototype.hasOwnProperty.call(change.patch,'BTTMenuItemImage'))continue;
        const data=$.NSData.alloc.initWithBase64EncodedStringOptions(change.patch.BTTMenuItemImage,0);
        const icon=$.NSImage.alloc.initWithData(data);
        if(!icon || !Number(icon.size.width) || !Number(icon.size.height)) {
            throw new Error('Cannot decode original icon for '+change.label+'; no changes made.');
        }
    }
}
function iconApply(btt, plan, fingerprint, validate, normalize=(value)=>value) {
    function get() {
        const raw=JSON.parse(btt.get_trigger(ICON_MEDIA));
        const hits=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===ICON_MEDIA);
        if(hits.length!==1 || !Array.isArray(hits[0].BTTMenuItems))throw new Error('Cannot export complete Media menu.');
        return hits[0];
    }
    const original=get();
    if(fingerprint(normalize(original,plan.media))!==fingerprint(plan.media))throw new Error('Media changed during backup; no changes made.');
    validate(plan.changes);
    const expected=JSON.parse(JSON.stringify(original));
    for(const change of plan.changes) {
        const matches=expected.BTTMenuItems.filter(x=>x.BTTUUID===change.uuid);
        if(matches.length!==1 || ![773,774].includes(Number(matches[0].BTTTriggerType)))throw new Error('Menu item changed; no changes made.');
        Object.assign(matches[0].BTTMenuConfig,change.patch);
    }
    for(const change of plan.changes) {
        btt.update_menu_item(change.uuid,{json:JSON.stringify(change.patch),persist:true});
    }
    if(fingerprint(normalize(get(),expected))!==fingerprint(expected))throw new Error('Readback differs from the appearance update (beyond verified image storage conversion).');
    return 'Updated '+plan.changes.length+' menu items; verified other menu settings unchanged.';
}
function run(argv) {
    const mode=argv[0],repo=argv[1];
    function read(path) {
        const text=$.NSString.stringWithContentsOfFileEncodingError(path,$.NSUTF8StringEncoding,null);
        if(!text)throw new Error('Cannot read '+path);
        return ObjC.unwrap(text);
    }
    if(mode==='normalize-configs') {
        const input=JSON.parse(read(argv[2])),equivalent=nativeIconMatcher(input.presetPath);
        return JSON.stringify(input.pairs.map(pair=>normalizeIconConfig(pair.actual,pair.expected,equivalent)));
    }
    if(mode==='main-style-plan') {
        const input=JSON.parse(read(argv[2]));
        iconValidate(input.icons);
        return JSON.stringify(mainStyleChanges(input,blackRTF));
    }
    if(mode==='validate') {
        iconValidate(JSON.parse(read(argv[2])).changes);
        return 'All original icon payloads decode as native macOS images.';
    }
    const btt=Application('/Applications/BetterTouchTool.app');
    if(mode==='snapshot') {
        const raw=JSON.parse(btt.get_trigger(ICON_MEDIA));
        const hits=(Array.isArray(raw)?raw:[raw]).filter(x=>x&&x.BTTUUID===ICON_MEDIA);
        if(hits.length!==1 || Number(hits[0].BTTTriggerType)!==767 || !Array.isArray(hits[0].BTTMenuItems)) {
            throw new Error('Cannot export complete Media menu.');
        }
        return JSON.stringify(hits[0]);
    }
    if(mode!=='apply')throw new Error('Unknown icon updater mode.');
    const fingerprint=new Function(read(repo+'/macbook/bettertouchtool/btt_common.js')+'\nreturn BTTCommon.fingerprint;')();
    const plan=JSON.parse(read(argv[2]));
    return iconApply(btt,plan,fingerprint,iconValidate,
        (actual,expected)=>normalizeIconTree(actual,expected,nativeIconMatcher(plan.presetPath)));
}
