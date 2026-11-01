// Evaluates the editing keys' trigger conditions with Foundation's NSPredicate,
// the engine BTT uses, so the installer and tests check BTT's own semantics.
// Input: {predicate, cases:[variables]}. Output: {format, results:[boolean]}.
ObjC.import('Foundation');

function run(argv) {
    const request = JSON.parse(argv[0]);
    const predicate = $.NSPredicate.predicateWithFormatArgumentArray($(request.predicate), $([]));
    return JSON.stringify({
        format: ObjC.unwrap(predicate.predicateFormat),
        results: request.cases.map(variables => Boolean(predicate.evaluateWithObject($(variables))))
    });
}
