// Evaluate common JXA helpers in the same VM as each test's Foundation mocks.
const fs = require('node:fs'), path = require('node:path');
const commonSource = fs.readFileSync(path.join(__dirname, '../bettertouchtool/btt_common.js'), 'utf8');
module.exports = {withCommon: source => commonSource + '\n' + source};
