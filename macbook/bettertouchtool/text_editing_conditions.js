// Evaluates the editing keys' trigger conditions with Foundation's NSPredicate,
// the engine BTT uses, so the installer and tests check BTT's own semantics.
// Input: {predicate, cases:[variables]}. Output: {format, results:[boolean]}.
ObjC.import('Foundation');

// Foundation treats missing dictionary keys as nil, unlike BTT's variable
// resolver, which can abort a condition before mismatch pass-through happens.
function predicateKeys(predicate) {
    const keys = new Set();
    function expressionKeys(expression) {
        const type = Number(expression.expressionType);
        if (type === 3) keys.add(ObjC.unwrap(expression.keyPath)); // NSKeyPathExpressionType
        else if (type === 14) { // NSAggregateExpressionType (IN {...})
            const children = expression.collection;
            for (let i = 0; i < Number(children.count); i++) expressionKeys(children.objectAtIndex(i));
        } else if (type !== 0) throw new Error('Unsupported expression kind in truth table: ' + type);
    }
    function visit(rule) {
        if (rule.isKindOfClass($.NSCompoundPredicate)) {
            const children = rule.subpredicates;
            for (let i = 0; i < Number(children.count); i++) visit(children.objectAtIndex(i));
            return;
        }
        if (!rule.isKindOfClass($.NSComparisonPredicate)) throw new Error('Unsupported predicate kind in truth table.');
        expressionKeys(rule.leftExpression);
        expressionKeys(rule.rightExpression);
    }
    visit(predicate);
    return [...keys];
}

function run(argv) {
    const request = JSON.parse(argv[0]);
    const predicate = $.NSPredicate.predicateWithFormatArgumentArray($(request.predicate), $([]));
    const keys = predicateKeys(predicate);
    for (let i = 0; i < request.cases.length; i++) {
        for (const key of keys) {
            if (!Object.prototype.hasOwnProperty.call(request.cases[i], key))
                throw new Error('Truth-table case ' + i + ' does not define condition variable: ' + key);
        }
    }
    return JSON.stringify({
        format: ObjC.unwrap(predicate.predicateFormat),
        results: request.cases.map(variables => Boolean(predicate.evaluateWithObject($(variables))))
    });
}
