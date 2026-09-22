// Shared JXA safety helpers. Loading this file never contacts BetterTouchTool.
ObjC.import('Foundation');
var BTTCommon = (function () {
    const volatile = new Set(['BTTLastUpdatedAt', 'BTTLastChangeUUID']);
    function clone(value) { return JSON.parse(JSON.stringify(value)); }
    function canonical(value) {
        if (Array.isArray(value)) {
            const items = value.map(canonical);
            if (items.length && items.every(x => x && typeof x === 'object' && x.BTTUUID))
                items.sort((a, b) => a.BTTUUID.localeCompare(b.BTTUUID));
            return items;
        }
        if (value && typeof value === 'object') {
            const result = {};
            Object.keys(value).sort().forEach(key => {
                if (!volatile.has(key)) result[key] = canonical(value[key]);
            });
            return result;
        }
        return value;
    }
    function fingerprint(value) { return JSON.stringify(canonical(value)); }
    function readJSON(path) {
        const value = $.NSString.stringWithContentsOfFileEncodingError(path, $.NSUTF8StringEncoding, null);
        if (!value) throw new Error('Cannot read ' + path);
        return JSON.parse(ObjC.unwrap(value));
    }
    function backup(snapshot, directory) {
        const manager = $.NSFileManager.defaultManager;
        if (!manager.createDirectoryAtPathWithIntermediateDirectoriesAttributesError(
            directory, false, $({NSFilePosixPermissions: 448}), null))
            throw new Error('Cannot create private backup directory; no BTT changes made.');
        const path = directory + '/backup.json';
        if (!$(JSON.stringify(snapshot, null, 2)).writeToFileAtomicallyEncodingError(
            path, true, $.NSUTF8StringEncoding, null) ||
            !manager.setAttributesOfItemAtPathError($({NSFilePosixPermissions: 384}), path, null) ||
            JSON.stringify(readJSON(path)) !== JSON.stringify(snapshot))
            throw new Error('Backup verification failed; no BTT changes made.');
        return path;
    }
    return {clone, canonical, fingerprint, readJSON, backup};
})();
