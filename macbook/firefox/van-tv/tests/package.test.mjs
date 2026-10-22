import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import '../config.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const config = globalThis.VAN_TV_CONFIG;

test('XPI builds without dependencies and contains a self-contained manifest payload', async () => {
  const output = execFileSync('/bin/zsh', [`${root}build.zsh`], { encoding: 'utf8' }).trim();
  assert.equal(output, `${root}dist/van-tv.xpi`);
  const files = execFileSync('/usr/bin/unzip', ['-Z1', output], { encoding: 'utf8' }).trim().split('\n');
  const manifest = JSON.parse(execFileSync('/usr/bin/unzip', ['-p', output, 'manifest.json'], { encoding: 'utf8' }));
  assert.deepEqual(manifest, JSON.parse(await readFile(`${root}manifest.json`, 'utf8')));
  assert.equal(manifest.manifest_version, 2);
  assert.equal(manifest.version, '1.2.0');
  assert.equal(manifest.browser_specific_settings.gecko.id, 'van-tv@lionfeather.dev');
  const hosts = config.receiverUrls.map(url => `http://${new URL(url).hostname}/*`);
  assert.deepEqual(manifest.permissions, ['contextMenus', 'tabs', ...hosts]);
  assert.deepEqual(manifest.content_scripts[0].matches, config.youtube.contentMatches);
  assert.deepEqual(Object.keys(manifest.commands), [config.command]);
  assert.equal(manifest.commands[config.command].suggested_key.default, 'Alt+Shift+V');
  const required = [manifest.background.page, manifest.browser_action.default_popup,
    manifest.browser_action.default_icon, ...manifest.content_scripts.flatMap(item => [...item.js, ...item.css])];
  for (const path of required) assert.ok(files.includes(path), `Packaged ${path}`);
  assert.ok(files.includes('receiver-client.mjs'));
  assert.ok(files.includes('youtube-video.mjs'));
  assert.ok(files.every(path => !path.includes('/') && !path.endsWith('.test.mjs')));
  assert.equal(files.includes('build.zsh'), false);
  assert.equal(files.includes('README.md'), false);
  assert.equal(manifest.web_accessible_resources, undefined);
});
