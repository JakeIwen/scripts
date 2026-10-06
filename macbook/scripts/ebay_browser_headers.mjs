// Use the managed clean MCP service; never launch or attach a browser here.
import { createRequire } from 'node:module';

const requireBrowser = createRequire(
  '/Users/jacobr/.local/share/codex-browser-tools/doctor.mjs',
);
const { Client } = requireBrowser('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = requireBrowser(
  '@modelcontextprotocol/sdk/client/streamableHttp.js',
);

const url = new URL(process.argv[2]);
if (url.origin !== 'https://www.ebay.com' || url.pathname !== '/sch/i.html' ||
    !url.searchParams.get('_nkw') || url.username || url.password) {
  throw new Error('Expected an anonymous eBay saved-search URL');
}
const client = new Client({ name: 'deal-watch-browser-refresh', version: '1.0.0' });
const transport = new StreamableHTTPClientTransport(new URL('http://localhost:8931/mcp'));
let stage = 'connecting to the managed browser';

try {
  await client.connect(transport);
  stage = 'loading the signed-out eBay search';
  const result = await client.callTool({
    name: 'browser_run_code_unsafe',
    arguments: { code: `async (page) => {
      const url = ${JSON.stringify(url.href)};
      let step = 'homepage';
      try {
      await page.goto('https://www.ebay.com/', {waitUntil: 'load', timeout: 20000});
      // Homepage scripts initialize anonymous cookies after the document loads.
      await page.waitForTimeout(3000);
      step = 'search navigation';
      await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 20000});
      step = 'waiting for listings';
      await page.waitForFunction(() => {
        return location.pathname === '/sch/i.html' &&
          document.querySelector('.srp-results, .srp-river-results, .srp-save-null-search');
      }, null, {timeout: 45000});
      step = 'confirming signed-out state';
      const signedOut = await page.locator('#gh').innerText({timeout: 5000});
      if (!/sign in/i.test(signedOut)) throw new Error('Signed-out state not confirmed');
      const cookies = await page.context().cookies(url);
      const userAgent = await page.evaluate(() => navigator.userAgent);
      if (!cookies.length) throw new Error('No browser cookies received');
      return {headers: 'User-Agent: ' + userAgent + '\\nCookie: ' +
        cookies.map(c => c.name + '=' + c.value).join('; ') + '\\n'};
      } catch { return {failedStep: step}; }
    }` },
  }, undefined, { timeout: 90000 });
  if (result.isError) throw new Error('Anonymous browser did not complete the search');
  const text = result.content.filter(part => part.type === 'text').map(part => part.text).join('\n');
  const match = text.match(/### Result\s*\n([^\n]+)/);
  const payload = JSON.parse(match?.[1] ?? 'null');
  if (payload?.failedStep) {
    stage = payload.failedStep;
    throw new Error('Browser did not reach a signed-out results page');
  }
  stage = 'reading browser headers';
  if (!payload || typeof payload.headers !== 'string' || payload.headers.length > 65536) {
    throw new Error('Browser returned invalid headers');
  }
  // Only the parent process reads stdout; it is never forwarded to a log.
  process.stdout.write(JSON.stringify(payload));
} catch {
  console.error('eBay anonymous browser refresh failed at ' + stage + '; existing headers retained.');
  process.exitCode = 1;
} finally {
  try { await client.callTool({ name: 'browser_close', arguments: {} }, undefined, {timeout: 5000}); } catch {}
  try { await transport.terminateSession(); } catch {}
  await client.close();
}
