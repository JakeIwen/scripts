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

export async function readSavedSearch(page, url) {
  const responses = [];
  let successfulRequest;
  page.on('response', response => {
    if (!response.request().isNavigationRequest() || response.request().frame() !== page.mainFrame()) return;
    responses.push(response.status());
    if (response.status() === 200 && response.url().startsWith('https://www.ebay.com/sch/i.html?')) {
      successfulRequest = response.request().allHeaders();
    }
  });
  let step = 'opening the saved search';
  try {
    const first = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 20000});
    // A bare 403 never runs verification. Do not interrupt a challenge already running.
    if (first.status() === 403) {
      step = 'anonymous browser verification';
      await page.goto('https://www.ebay.com/splashui/challenge?ap=1&appName=orch&ru=' +
        encodeURIComponent(url), {waitUntil: 'domcontentloaded', timeout: 20000});
    }
    step = 'waiting for listings';
    await page.waitForFunction(() => location.pathname === '/sch/i.html' && (
      document.querySelector('.srp-results, .srp-river-results, .srp-save-null-search') ||
      document.title === 'Error Page | eBay'
    ), null, {timeout: 45000});
    if (!await page.locator('.srp-results, .srp-river-results, .srp-save-null-search').count()) {
      throw new Error('Saved search was rejected');
    }
    step = 'waiting for session cookies';
    await page.waitForLoadState('load', {timeout: 15000});
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
    const headers = {...await successfulRequest};
    const cookies = await page.context().cookies(url);
    headers.cookie = cookies.map(cookie => cookie.name + '=' + cookie.value).join('; ');
    if (!headers?.cookie || !headers['user-agent']) throw new Error('Missing document headers');
    return {requestHeaders: headers};
  } catch {
    return {failedStep: step, responses: responses.slice(-8)};
  }
}

export async function captureAnonymousSession(managedPage, options) {
  const {context, page} = await createAnonymousPage(managedPage, options);
  try {
    const result = await readSavedSearch(page, options.url);
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

export function serializeRequestHeaders(headers, schema) {
  if (!headers || typeof headers !== 'object') throw new Error('Missing request headers');
  const allowed = new Set([...schema.required, ...schema.optional]);
  const entries = Object.entries(headers).filter(([name]) => allowed.has(name));
  if (schema.required.some(name => !headers[name])) throw new Error('Missing required headers');
  if (entries.some(([, value]) => typeof value !== 'string' || !value || /[\r\n\0]/.test(value))) {
    throw new Error('Invalid request header');
  }
  const text = entries.map(([name, value]) => `${name}: ${value}`).join('\n') + '\n';
  if (Buffer.byteLength(text) > 65536) throw new Error('Request headers are too large');
  return text;
}
