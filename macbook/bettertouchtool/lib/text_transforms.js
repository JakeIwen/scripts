// Selection-only transforms. Case conversion must preserve prose whitespace.
var TextTransforms = (function () {
    const kinds = ['lower', 'upper', 'snake', 'kebab', 'camel',
                   'base64-encode', 'base64-decode', 'uri-encode', 'uri-decode'];

    function transform(kind, text, dependencies) {
        if (!kinds.includes(kind)) throw new Error('Unknown text transform.');
        if (typeof text !== 'string') throw new Error('Selection is not text.');
        switch (kind) {
        case 'lower': return text.toLowerCase();
        case 'upper': return text.toUpperCase();
        case 'uri-encode': return encodeURI(text);
        case 'uri-decode': return decodeURI(text);
        case 'base64-encode': return dependencies.encodeBase64(text);
        case 'base64-decode': {
            const compact = text.replace(/\s/g, '');
            if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(compact))
                throw new Error('Selection is not valid Base64.');
            const decoded = dependencies.decodeBase64(compact);
            if (typeof decoded !== 'string') throw new Error('Base64 does not contain UTF-8 text.');
            return decoded;
        }
        default:
            return text.replace(/[^\r\n]+/g, line => dependencies.cases[kind + 'Case'](line));
        }
    }

    function replace(kind, ui, dependencies) {
        const selection = ui.read();
        if (!selection.text) throw new Error('Select editable text first.');
        if (!selection.writable) throw new Error('This app does not support direct selection replacement.');
        const result = transform(kind, selection.text, dependencies);
        if (result === selection.text) return 'unchanged';
        // A failed decode or changed focus must never reach the write operation.
        if (!ui.same(selection, ui.read())) throw new Error('Selection changed; please try again.');
        ui.write(selection, result);
        return 'replaced';
    }
    return {kinds, transform, replace};
})();
if (typeof module !== 'undefined') module.exports = TextTransforms;
