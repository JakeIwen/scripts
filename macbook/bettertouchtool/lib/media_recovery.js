// Pure recovery planning helpers; no application calls or live writes.
ObjC.import('Foundation');

const CONTROL_MEDIA = 'D9B0ED12-C4BE-4E74-B0DA-0CC3BE092289';
const CONTROL_SPEAKERS = [
    {uuid:'D6D639BB-B1D1-4C90-85E3-B8F5C8310E56', title:'rear spk', minimum:4},
    {uuid:'F3CC5F44-0D08-49DD-8B5C-3D6C1C022759', title:'Sonos', minimum:9}
];

function controlsRead(path) {
    const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
    if (!value) throw new Error('Cannot read ' + path);
    return ObjC.unwrap(value);
}
function controlsClone(value) { return JSON.parse(JSON.stringify(value)); }
function controlsRestoreSpeaker(current, archived, sizeHelpers) {
    const restored = controlsClone(current);
    function merge(live, saved) {
        for (const key of ['BTTMenuItems','BTTMenuItemActions','BTTAdditionalActions']) {
            const archivedItems = saved[key] || [];
            if (!archivedItems.length) continue;
            const children = live[key] || (live[key] = []);
            for (const old of archivedItems) {
                const existing = children.find(x => x.BTTUUID === old.BTTUUID);
                if (existing) merge(existing, old);
                else children.push(controlsClone(old));
            }
        }
    }
    merge(restored, archived);
    function fixSizes(item) {
        for (const edit of sizeHelpers.edits(item.BTTMenuConfig, [])) {
            sizeHelpers.set(item.BTTMenuConfig, edit.path, edit.after);
        }
        (item.BTTMenuItems || []).forEach(fixSizes);
    }
    fixSizes(restored);
    return restored;
}
function controlsArchives(repo) {
    const fm = $.NSFileManager.defaultManager, directory=repo+'/tmp';
    const names=ObjC.deepUnwrap(fm.contentsOfDirectoryAtPathError(directory,null));
    if (!Array.isArray(names)) throw new Error('Cannot list recovery backups.');
    return names.filter(x => /^btt-(?:notes|labels|sizing)-backup-/.test(x)).map(name => {
        const path=directory+'/'+name+'/backup.json';
        try {
            const data=JSON.parse(controlsRead(path));
            const root=data.media || data.mediaExport;
            const attrs=fm.attributesOfItemAtPathError(path,null);
            const modified=Number(attrs.objectForKey('NSFileModificationDate').timeIntervalSince1970);
            return {path,root,modified};
        } catch (_) {return null;}
    }).filter(x => x && x.root && x.root.BTTUUID===CONTROL_MEDIA)
        .sort((a,b)=>b.modified-a.modified);
}
