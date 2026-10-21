import assert from 'node:assert/strict';
import test from 'node:test';
import { createMessageHandler, installCommands, installContextMenus } from '../background-actions.mjs';
import { createReceiverClient } from '../receiver-client.mjs';
import { castTab, tabVideo } from '../tab-casting.mjs';
import { startReceiver, videoId } from './receiver-fixture.mjs';

const config = globalThis.VAN_TV_CONFIG;
const tab = { id: 4, url: `https://www.youtube.com/watch?v=${videoId}&t=15` };

function browserFixture({ current = { videoId, position: 64 }, noScript = false } = {}) {
  const messages = [], menus = [], titles = [], badges = [];
  let onClick, onCommand, finish;
  const completed = new Promise(resolve => { finish = resolve; });
  const browserApi = {
    tabs: {
      query: async () => [tab],
      sendMessage: async (id, message) => {
        messages.push({ id, ...message });
        if (noScript) throw new Error('No content script');
        return message.type === config.message.getVideo ? current : undefined;
      },
    },
    contextMenus: {
      removeAll: async () => {}, create: menu => menus.push(menu),
      onClicked: { addListener: callback => { onClick = callback; } },
    },
    commands: { onCommand: { addListener: callback => { onCommand = callback; } } },
    browserAction: {
      setBadgeText: async value => badges.push(value.text),
      setTitle: async value => { titles.push(value.title); finish(); },
    },
  };
  return { browserApi, messages, menus, titles, badges, completed,
    click: info => onClick(info, tab), command: name => onCommand(name) };
}

test('popup cast uses the current tab playhead and pauses only after an accepted HTTP cast', async t => {
  const fixture = await startReceiver(t);
  const browser = browserFixture();
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  const handle = createMessageHandler(browser.browserApi, receiver);
  const result = await handle({ type: config.message.castTab }, {});
  assert.deepEqual(result, { ok: true, data: { videoId, position: 64 } });
  assert.deepEqual(browser.messages, [
    { id: tab.id, type: config.message.getVideo, url: tab.url, videoId },
    { id: tab.id, type: config.message.castAccepted, videoId, url: tab.url },
  ]);
  assert.equal(fixture.requests.find(request => request.method === 'POST').body.position, 64);
});

test('unavailable content script falls back to URL timestamp, including short links', async t => {
  const fixture = await startReceiver(t);
  const browser = browserFixture({ noScript: true });
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  assert.deepEqual(await castTab(browser.browserApi, receiver, tab), { videoId, position: 15 });
  const shortTab = { id: 5, url: `https://youtu.be/${videoId}?t=2m` };
  assert.deepEqual(await tabVideo(browser.browserApi, shortTab), { videoId, position: 120 });
  assert.equal(await tabVideo(browser.browserApi, { url: 'https://example.test/' }), null);
});

test('a video page without a usable media playhead falls back to its URL timestamp', async () => {
  const browser = browserFixture({ current: { videoId, position: null } });
  assert.deepEqual(await tabVideo(browser.browserApi, tab), { videoId, position: 15 });
  const invalid = browserFixture({ current: { videoId, position: '90' } });
  await assert.rejects(tabVideo(invalid.browserApi, tab), /Invalid tab playback position/);
});

test('tab navigation during the playhead query refuses a stale cast', async t => {
  const fixture = await startReceiver(t);
  const browser = browserFixture({ current: null });
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  await assert.rejects(castTab(browser.browserApi, receiver, tab), /tab changed/);
  assert.equal(fixture.requests.length, 0);
});

test('failed casts never send a pause acknowledgement', async t => {
  const fixture = await startReceiver(t, { respond(request, response) {
    if (request.method !== 'POST') return false;
    response.writeHead(400); response.end('{}'); return true;
  } });
  const browser = browserFixture();
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  const handle = createMessageHandler(browser.browserApi, receiver);
  assert.deepEqual(await handle({ type: config.message.castTab }, {}), {
    ok: false, error: 'Van TV request failed (HTTP 400)',
  });
  assert.equal(browser.messages.some(message => message.type === config.message.castAccepted), false);
});

test('unknown messages and content-script receiver controls have no side effects', async t => {
  const fixture = await startReceiver(t);
  const browser = browserFixture();
  const receiver = createReceiverClient({ baseUrls: [fixture.url] });
  const handle = createMessageHandler(browser.browserApi, receiver);
  assert.equal(handle({ type: 'toString' }, {}), undefined);
  assert.equal(handle({ type: config.message.control, action: 'stop' }, { tab }), undefined);
  assert.equal(handle({ type: config.message.status }, { tab }), undefined);
  assert.equal(fixture.requests.length, 0);
  assert.deepEqual(await handle({ type: config.message.cast, videoId, position: 9 }, { tab }), {
    ok: true, data: { videoId, position: 9 },
  });
});

test('link context menu deliberately ignores URL timestamp and leaves local playback alone', async t => {
  const fixture = await startReceiver(t);
  const browser = browserFixture();
  await installContextMenus(browser.browserApi, createReceiverClient({ baseUrls: [fixture.url] }));
  const menu = browser.menus.find(item => item.contexts.includes('link'));
  assert.equal(menu.title, 'Cast to Van TV');
  assert.ok(menu.targetUrlPatterns.includes('*://youtu.be/*'));
  browser.click({ menuItemId: menu.id, linkUrl: `https://youtu.be/${videoId}?t=90` });
  await browser.completed;
  assert.deepEqual(fixture.requests.find(request => request.method === 'POST').body, { videoId, position: 90 });
  assert.equal(browser.messages.length, 0);
  assert.deepEqual(browser.badges, ['']);
});

test('watch-page menu and shortcut use the same active playhead cast flow', async t => {
  for (const source of ['page', 'shortcut']) {
    const fixture = await startReceiver(t);
    const browser = browserFixture();
    const receiver = createReceiverClient({ baseUrls: [fixture.url] });
    if (source === 'page') {
      await installContextMenus(browser.browserApi, receiver);
      const menu = browser.menus.find(item => item.contexts.includes('page'));
      browser.click({ menuItemId: menu.id });
    } else {
      installCommands(browser.browserApi, receiver);
      browser.command(config.command);
    }
    await browser.completed;
    assert.equal(fixture.requests.find(request => request.method === 'POST').body.position, 64);
    assert.equal(browser.messages.at(-1).type, config.message.castAccepted);
  }
});

test('shortcut failure is visible in the toolbar badge and hover title', async t => {
  const fixture = await startReceiver(t, { respond(request, response) {
    response.writeHead(503); response.end('{}'); return true;
  } });
  const browser = browserFixture();
  installCommands(browser.browserApi, createReceiverClient({ baseUrls: [fixture.url] }));
  browser.command(config.command);
  await browser.completed;
  assert.deepEqual(browser.badges, ['!']);
  assert.match(browser.titles[0], /receiver is not running/);
  assert.equal(browser.messages.some(message => message.type === config.message.castAccepted), false);
});
