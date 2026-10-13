// Evaluate common JXA helpers in the same VM as each test's Foundation mocks.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

const commonSource = fs.readFileSync(path.join(__dirname, '../bettertouchtool/btt_common.js'), 'utf8');
const installerDirectory = path.join(__dirname, '../bettertouchtool');

function createMediaToolsFixture() {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'btt-media-tools-'));
    const resources = path.join(root, 'build/Performance Audio.app/Contents/Resources');
    fs.mkdirSync(resources, {recursive: true});
    for (const name of ['performance_audio.py', 'convert_video.py', 'conversion_editor.py',
                        'bpm_over_time.py', 'rhythm_practice.py', 'interval_analysis.py']) {
        fs.writeFileSync(path.join(resources, name), '# fixture resource\n');
    }
    fs.writeFileSync(path.join(resources, 'python-path.txt'), '/usr/bin/python3\n');
    fs.writeFileSync(path.join(resources, 'bpm-python-path.txt'), '/usr/bin/python3\n');
    const presets = {presets: {fixture: {label: 'Fixture preset'}}};
    const presetPath = path.join(root, 'scripts/performance_audio_presets.json');
    fs.mkdirSync(path.dirname(presetPath), {recursive: true});
    fs.writeFileSync(presetPath, JSON.stringify(presets) + '\n');
    const resolvedRoot = fs.realpathSync(root);
    const env = {...process.env, MAC_MEDIA_TOOLS: resolvedRoot};
    process.once('exit', () => fs.rmSync(resolvedRoot, {recursive: true, force: true}));
    return {root: resolvedRoot, env};
}

function generateMenuDefinition(mediaToolsFixture, moduleName, argument) {
    const call = argument === undefined ? 'definition()' : `definition(${JSON.stringify(argument)})`;
    const source = `import json; from ${moduleName} import definition; print(json.dumps(${call}))`;
    return JSON.parse(execFileSync('/usr/bin/python3', ['-c', source], {
        cwd: installerDirectory,
        env: mediaToolsFixture.env,
        encoding: 'utf8',
    }));
}

function shellArguments(command) {
    const source = 'import json, shlex, sys; print(json.dumps(shlex.split(sys.argv[1])))';
    return JSON.parse(execFileSync('/usr/bin/python3', ['-c', source, command], {encoding: 'utf8'}));
}

module.exports = {
    withCommon: source => commonSource + '\n' + source,
    createMediaToolsFixture,
    generateMenuDefinition,
    shellArguments,
};
