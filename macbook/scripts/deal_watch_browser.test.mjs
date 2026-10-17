import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {createAnonymousPage, readSavedSearch, serializeBrowserPage} from './deal_watch_browser.mjs';

const limits = JSON.parse(fs.readFileSync(new URL(
  '../../pi/scripts/price_check/search_watch/browser_capture_limits.json', import.meta.url), 'utf8'));
const url = 'https://www.ebay.com/sch/i.html?_nkw=example&_udlo=450';
const html = '<html><body><ul class="srp-results"><li>Result</li></ul></body></html>';

function browserPage({initial = 403, signedOut = true, sameSearch = true, blocked = false, domNotReady = false} = {}) {
  const destinations = [];
  let listener;
  const frame = {};
  const document = {
    status: () => 200, url: () => url,
    request: () => ({isNavigationRequest: () => true, frame: () => frame,
      allHeaders: async () => {throw new Error("Request credentials must not be exported");}}),
  };
  return {
    destinations, on: (_event, handler) => {listener = handler;}, mainFrame: () => frame,
    goto: async destination => {
      destinations.push(destination);
      if (!blocked && (initial !== 403 || destinations.length === 2)) listener(document);
      return {status: () => destinations.length === 1 ? initial : 200};
    },
    waitForFunction: async () => {if (domNotReady) throw new Error('Document is still parsing');},
    waitForLoadState: async () => {throw new Error('Unrelated resource loads must not block results');},
    waitForTimeout: async () => {},
    context: () => ({cookies: async () => {throw new Error('Cookie export is forbidden');}}),
    url: () => url,
    evaluate: async (_code, argument) => typeof argument === 'number' ? html : sameSearch,
    locator: selector => ({count: async () => blocked ? 0 : 1,
      innerText: async () => selector === '#gh' && signedOut ? 'Hi! Sign in or register' : 'Hi Jacob!'}),
  };
}

test('a bare rejection enters verification and exports the rendered page', async () => {
  const page = browserPage();
  assert.deepEqual(await readSavedSearch(page, url, limits.max_html_bytes), {final_url: url, html});
  assert.equal(page.destinations.length, 2);
  assert.equal(new URL(page.destinations[1]).searchParams.get('ru'), url);
});

test('an automatic challenge is allowed to finish without a second navigation', async () => {
  const page = browserPage({initial: 200});
  assert.deepEqual(await readSavedSearch(page, url, limits.max_html_bytes), {final_url: url, html});
  assert.deepEqual(page.destinations, [url]);
});

for (const settings of [{signedOut: false}, {sameSearch: false}, {blocked: true}, {domNotReady: true}]) {
  test(`unsafe or unsuccessful responses export no page: ${JSON.stringify(settings)}`, async () => {
    const result = await readSavedSearch(browserPage(settings), url, limits.max_html_bytes);
    assert.ok(result.failedStep);
    assert.equal(result.html, undefined);
    assert.ok(!JSON.stringify(result).includes('anonymous=private'));
  });
}

test('page export contains only the final URL and rendered HTML', () => {
  const page = {final_url: url, html};
  assert.deepEqual(JSON.parse(serializeBrowserPage(page, limits)), page);
  assert.throws(() => serializeBrowserPage({...page, cookie: 'private=value'}, limits));
  assert.throws(() => serializeBrowserPage({...page, search_id: 42}, limits));
});

test('page export rejects malformed, oversized and non-string data', () => {
  for (const bad of [{}, {final_url: url, html: ''}, {final_url: url, html: []},
    {final_url: null, html}, {final_url: url, html: 'é'.repeat(limits.max_html_bytes / 2 + 1)}]) {
    assert.throws(() => serializeBrowserPage(bad, limits));
  }
  assert.throws(() => serializeBrowserPage({final_url: url, html}, {...limits, max_submission_bytes: 10}));
});

test('the browser user agent and client hints use the same native Chromium version', async () => {
  let options, override;
  const page = {};
  const context = {newPage: async () => page,
    newCDPSession: async () => ({send: async (_method, value) => {override = value;}})};
  const managed = {
    goto: async () => {},
    evaluate: async () => ({ua: 'Mozilla HeadlessChrome/151.2.3.4', platform: 'MacIntel', metadata: {
      brands: [{brand: 'HeadlessChrome', version: '151'}, {brand: 'Chromium', version: '151'}],
      fullVersionList: [{brand: 'HeadlessChrome', version: '151.2.3.4'}, {brand: 'Chromium', version: '151.2.3.4'}],
      uaFullVersion: '151.2.3.4', architecture: 'arm', platform: 'macOS', mobile: false,
    }}),
    context: () => ({browser: () => ({newContext: async value => {options = value; return context;}})}),
  };
  const result = await createAnonymousPage(managed, {metadataOrigin: 'http://localhost:8931'});
  assert.equal(result.page, page);
  assert.equal(options.userAgent, override.userAgent);
  assert.equal(override.userAgent, 'Mozilla Chrome/151.2.3.4');
  assert.equal(override.userAgentMetadata.fullVersion, '151.2.3.4');
  assert.deepEqual(override.userAgentMetadata.brands, [{brand: 'Chromium', version: '151'}]);
  assert.equal(override.userAgentMetadata.architecture, 'arm');
});
