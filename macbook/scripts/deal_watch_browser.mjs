/** Browser-only functions serialized into the existing clean MCP service. */

export async function createAnonymousPage(managedPage, options) {
  // Loopback is a secure context for reading Chromium's native client hints.
  await managedPage.goto(options.metadataOrigin, {waitUntil: 'domcontentloaded', timeout: 5000});
  const source = await managedPage.evaluate(async () => ({
    ua: navigator.userAgent, platform: navigator.platform,
    metadata: await navigator.userAgentData.getHighEntropyValues([
      'architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList',
    ]),
  }));
  const userAgent = source.ua.replace('HeadlessChrome/', 'Chrome/');
  const metadata = {...source.metadata,
    brands: source.metadata.brands.filter(b => b.brand !== 'HeadlessChrome'),
    fullVersionList: source.metadata.fullVersionList.filter(b => b.brand !== 'HeadlessChrome'),
    fullVersion: source.metadata.uaFullVersion,
  };
  delete metadata.uaFullVersion;
  const context = await managedPage.context().browser().newContext({
    ...(options.storageState ? {storageState: options.storageState} : {}),
    locale: 'en-US', userAgent,
  });
  try {
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    // Keep HTTP client hints and navigator's identity consistent with this UA.
    await cdp.send('Emulation.setUserAgentOverride', {
      userAgent, acceptLanguage: 'en-US,en;q=0.9', platform: source.platform,
      userAgentMetadata: metadata,
    });
    return {context, page};
  } catch (error) {
    await context.close();
    throw error;
  }
}

export async function readSavedSearch(page, url, maxHtmlBytes) {
  const responses = [];
  let documentStatus;
  page.on('response', response => {
    if (!response.request().isNavigationRequest() || response.request().frame() !== page.mainFrame()) return;
    responses.push(response.status());
    documentStatus = response.status();
  });
  let step = 'opening the saved search';
  try {
    const first = await page.goto(url, {waitUntil: 'commit', timeout: 20000});
    // A bare 403 never runs verification. Do not interrupt a challenge already running.
    if (first.status() === 403) {
      step = 'anonymous browser verification';
      await page.goto('https://www.ebay.com/splashui/challenge?ap=1&appName=orch&ru=' +
        encodeURIComponent(url), {waitUntil: 'commit', timeout: 20000});
    }
    step = 'waiting for listings';
    await page.waitForFunction(() => document.readyState !== 'loading' && location.pathname === '/sch/i.html' && (
      document.querySelector('.srp-results, .srp-river-results, .srp-save-null-search') ||
      document.title === 'Error Page | eBay'
    ), null, {timeout: 45000});
    if (!await page.locator('.srp-results, .srp-river-results, .srp-save-null-search').count()) {
      throw new Error('Saved search was rejected');
    }
    step = 'waiting for rendered results';
    // The parsed document is enough; image/ad completion is unrelated to results.
    await page.waitForTimeout(2000);
    const sameSearch = await page.evaluate(expected => {
      const actual = new URL(location.href), wanted = new URL(expected);
      return actual.origin === wanted.origin && actual.pathname === wanted.pathname &&
        [...wanted.searchParams].every(([key, value]) => actual.searchParams.getAll(key).includes(value));
    }, url);
    if (!sameSearch || !await page.locator('.srp-results, .srp-river-results, .srp-save-null-search').count()) {
      throw new Error('Saved search did not load');
    }
    step = 'confirming signed-out state';
    if (!/sign in/i.test(await page.locator('#gh').innerText({timeout: 5000}))) {
      throw new Error('Signed-out state not confirmed');
    }
    if (documentStatus !== 200) throw new Error('No successful document response');
    step = 'capturing search results';
    const html = await page.evaluate(limit => {
      const document = window.document.documentElement.cloneNode(true);
      document.querySelectorAll('script, style, noscript, iframe, template, meta, link, input[type="hidden"]').forEach(node => node.remove());
      const html = document.outerHTML;
      if (new TextEncoder().encode(html).byteLength > limit) throw new Error('Browser page is too large');
      return html;
    }, maxHtmlBytes);
    return {final_url: page.url(), html};
  } catch {
    return {failedStep: step, responses: responses.slice(-8)};
  }
}

export async function captureAnonymousSession(managedPage, options) {
  const {context, page} = await createAnonymousPage(managedPage, options);
  try {
    const result = await readSavedSearch(page, options.url, options.maxHtmlBytes);
    if (!result.failedStep) await context.storageState({path: options.candidateState});
    return result;
  } finally {
    await context.close();
  }
}

export function browserScript(options) {
  return `async (managedPage) => {
    const createAnonymousPage = ${createAnonymousPage.toString()};
    const readSavedSearch = ${readSavedSearch.toString()};
    const captureAnonymousSession = ${captureAnonymousSession.toString()};
    return captureAnonymousSession(managedPage, ${JSON.stringify(options)});
  }`;
}

export function serializeBrowserPage(page, limits) {
  if (!page || typeof page !== 'object' || Object.keys(page).sort().join(',') !== 'final_url,html') {
    throw new Error('Invalid browser page');
  }
  if (typeof page.html !== 'string' || !page.html || Buffer.byteLength(page.html) > limits.max_html_bytes ||
      typeof page.final_url !== 'string' || !page.final_url || page.final_url.length > limits.max_url_characters) {
    throw new Error('Invalid browser page fields');
  }
  const text = JSON.stringify(page);
  if (Buffer.byteLength(text) > limits.max_submission_bytes) throw new Error('Browser submission is too large');
  return text;
}
