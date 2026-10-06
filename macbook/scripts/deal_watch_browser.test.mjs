import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import {createAnonymousPage, readSavedSearch, serializeRequestHeaders} from './deal_watch_browser.mjs';

const schema = JSON.parse(fs.readFileSync(new URL(
  '../../pi/scripts/price_check/search_watch/browser_headers.json', import.meta.url), 'utf8'));
const url = 'https://www.ebay.com/sch/i.html?_nkw=example&_udlo=450';
const headers = {'user-agent': 'Chromium', cookie: 'anonymous=private',
  'sec-ch-ua': '"Chromium";v="151"', 'sec-fetch-site': 'same-origin'};

function browserPage({initial = 403, signedOut = true, sameSearch = true, blocked = false, loadFailed = false} = {}) {
  const destinations = [];
  let listener;
  const frame = {};
  const document = {
    status: () => 200, url: () => url,
    request: () => ({isNavigationRequest: () => true, frame: () => frame,
      allHeaders: async () => headers}),
  };
  return {
    destinations, on: (_event, handler) => {listener = handler;}, mainFrame: () => frame,
    goto: async destination => {
      destinations.push(destination);
      if (!blocked && (initial !== 403 || destinations.length === 2)) listener(document);
      return {status: () => destinations.length === 1 ? initial : 200};
    },
    waitForFunction: async () => {},
    waitForLoadState: async () => {if (loadFailed) throw new Error('Page did not finish loading');},
    waitForTimeout: async () => {},
    context: () => ({cookies: async () => [{name: 'anonymous', value: 'fresh'}]}),
    evaluate: async () => sameSearch,
    locator: selector => ({count: async () => blocked ? 0 : 1,
      innerText: async () => selector === '#gh' && signedOut ? 'Hi! Sign in or register' : 'Hi Jacob!'}),
  };
}

test('a bare rejection enters verification and exports the successful document request', async () => {
  const page = browserPage();
  assert.deepEqual(await readSavedSearch(page, url), {requestHeaders: {...headers, cookie: 'anonymous=fresh'}});
  assert.equal(page.destinations.length, 2);
  assert.equal(new URL(page.destinations[1]).searchParams.get('ru'), url);
});

test('an automatic challenge is allowed to finish without a second navigation', async () => {
  const page = browserPage({initial: 200});
  assert.deepEqual(await readSavedSearch(page, url), {requestHeaders: {...headers, cookie: 'anonymous=fresh'}});
  assert.deepEqual(page.destinations, [url]);
});

for (const settings of [{signedOut: false}, {sameSearch: false}, {blocked: true}, {loadFailed: true}]) {
  test(`unsafe or unsuccessful results export no credentials: ${JSON.stringify(settings)}`, async () => {
    const result = await readSavedSearch(browserPage(settings), url);
    assert.ok(result.failedStep);
    assert.equal(result.requestHeaders, undefined);
    assert.ok(!JSON.stringify(result).includes('anonymous=private'));
  });
}

test('the header export preserves client hints and excludes pseudo-headers and authorization', () => {
  const text = serializeRequestHeaders({...headers, ':authority': 'www.ebay.com',
    authorization: 'Bearer should-not-export', host: 'www.ebay.com'}, schema);
  assert.ok(text.includes('sec-ch-ua: "Chromium";v="151"\n'));
  assert.ok(text.includes('sec-fetch-site: same-origin\n'));
  assert.ok(!text.includes('Bearer'));
  assert.ok(!text.includes(':authority'));
  assert.ok(!text.includes('host:'));
});

test('malformed, incomplete, and oversized credentials are rejected', () => {
  for (const bad of [{}, {...headers, cookie: 'value\r\nAuthorization: bad'},
    {...headers, cookie: 'x'.repeat(65536)}, {...headers, 'sec-ch-ua': null}]) {
    assert.throws(() => serializeRequestHeaders(bad, schema));
  }
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
