// Use the managed clean MCP service; never launch or attach a browser here.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { browserScript, serializeRequestHeaders } from './deal_watch_browser.mjs';

const requireBrowser = createRequire('/Users/jacobr/.local/share/codex-browser-tools/doctor.mjs');
const { Client } = requireBrowser('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = requireBrowser('@modelcontextprotocol/sdk/client/streamableHttp.js');
const endpoint = new URL('http://localhost:8931/mcp');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const schema = JSON.parse(fs.readFileSync(path.join(root,
  'pi/scripts/price_check/search_watch/browser_headers.json'), 'utf8'));
const url = new URL(process.argv[2]);
if (url.origin !== 'https://www.ebay.com' || url.pathname !== '/sch/i.html' ||
    !url.searchParams.get('_nkw') || url.username || url.password) {
  throw new Error('Expected an anonymous eBay saved-search URL');
}
const stateDirectory = path.join(root, 'tmp/deal-watch-refresh');
fs.mkdirSync(stateDirectory, {recursive: true, mode: 0o700});
fs.chmodSync(stateDirectory, 0o700);
const statePath = path.join(stateDirectory, 'browser-state.json');
if (fs.existsSync(statePath)) {
  const details = fs.lstatSync(statePath);
  if (!details.isFile() || (details.mode & 0o077)) throw new Error('Browser state must be a private file');
}
const temporaryDirectory = fs.mkdtempSync(path.join(stateDirectory, '.browser-state-'));
const candidateState = path.join(temporaryDirectory, 'state.json');
fs.closeSync(fs.openSync(candidateState, 'wx', 0o600));
const client = new Client({ name: 'deal-watch-browser-refresh', version: '1.0.0' });
const transport = new StreamableHTTPClientTransport(endpoint);
let stage = 'connecting to the managed browser';

try {
  await client.connect(transport);
  stage = 'loading the signed-out eBay search';
  const result = await client.callTool({name: 'browser_run_code_unsafe', arguments: {
    code: browserScript({url: url.href, metadataOrigin: endpoint.origin,
      storageState: fs.existsSync(statePath) ? statePath : undefined, candidateState}),
  }}, undefined, {timeout: 100000});
  if (result.isError) throw new Error('Anonymous browser did not complete the search');
  const text = result.content.filter(part => part.type === 'text').map(part => part.text).join('\n');
  const payload = JSON.parse(text.match(/### Result\s*\n([^\n]+)/)?.[1] ?? 'null');
  if (payload?.failedStep) {
    stage = payload.failedStep + ' (HTTP ' + payload.responses.join(', ') + ')';
    throw new Error('Browser did not reach a signed-out results page');
  }
  stage = 'reading browser headers';
  const headers = serializeRequestHeaders(payload?.requestHeaders, schema);
  fs.renameSync(candidateState, statePath);
  // The parent consumes stdout privately; never forward it to a log.
  process.stdout.write(JSON.stringify({headers}));
} catch {
  console.error('eBay anonymous browser refresh failed at ' + stage + '; existing headers retained.');
  process.exitCode = 1;
} finally {
  try { await client.callTool({name: 'browser_close', arguments: {}}, undefined, {timeout: 5000}); } catch {}
  try { await transport.terminateSession(); } catch {}
  await client.close();
  fs.rmSync(candidateState, {force: true});
  fs.rmdirSync(temporaryDirectory);
}
