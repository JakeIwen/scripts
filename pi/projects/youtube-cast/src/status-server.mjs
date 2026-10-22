import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { config } from './config.mjs';
import { isVideoId } from './video-id.mjs';
import { CONTROL_ACTIONS, seekArguments, audioDelayArguments } from './player-controls.mjs';

const MAX_POST_BODY = 256;
// Web pages can't forge these origins, and a JSON body already forces a CORS
// preflight this server never answers; the status page itself is same-origin.
const EXTENSION_ORIGINS = ['moz-extension://', 'chrome-extension://'];
const POST_ROUTES = ['/api/pairing', '/api/control', '/api/cast'];
const controlArguments = {
  seek: { parse: seekArguments, error: 'Seek needs one of position (>= 0) or offset' },
  audio_delay: { parse: audioDelayArguments, error: 'Audio delay needs exactly one of delay or offset (finite seconds)' },
};

const assets = {
  '/': ['index.html', 'text/html; charset=utf-8'],
  '/status.mjs': ['status.mjs', 'text/javascript; charset=utf-8'],
  '/status.css': ['status.css', 'text/css; charset=utf-8'],
};

export async function createStatusServer({ snapshot, pairing, control = async () => false,
  cast = async () => { throw new Error('Cast unavailable'); },
  hosts = ['vanpi.lan', 'localhost', '127.0.0.1', config.lanAddress] }) {
  const content = new Map(await Promise.all(Object.entries(assets).map(async ([path, [file, type]]) =>
    [path, { type, body: await readFile(new URL(`../web/${file}`, import.meta.url)) }])));
  return createServer((request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'");
    const json = (code, body) => {
      response.writeHead(code, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(body));
    };
    let url;
    try { url = new URL(request.url, `http://${request.headers.host}`); }
    catch { json(400, { error: 'Invalid request' }); return; }
    if (!hosts.includes(url.hostname)) { json(403, { error: 'Untrusted host' }); return; }
    if (request.method === 'GET' && url.pathname === '/health') {
      const state = snapshot();
      json(state.ok ? 200 : 503, state);
    } else if (request.method === 'GET' && url.pathname === '/api/status') {
      json(200, { ...snapshot(), pairing: pairing.snapshot() });
    } else if (request.method === 'POST' && POST_ROUTES.includes(url.pathname)) {
      if (!trustedOrigin(request.headers.origin, url.origin) || request.headers['content-type'] !== 'application/json') {
        json(403, { error: 'Trusted-origin JSON required' }); return;
      }
      if (url.pathname === '/api/pairing') {
        request.resume();
        json(202, { requested: pairing.refresh() });
      } else {
        const handler = url.pathname === '/api/cast' ? handleCast : handleControl;
        void handler(request, { control, cast }).then(([code, body]) => json(code, body));
      }
    } else if (request.method === 'GET' && content.has(url.pathname)) {
      const asset = content.get(url.pathname);
      response.writeHead(200, { 'Content-Type': asset.type });
      response.end(asset.body);
    } else json(404, { error: 'Not found' });
  });
}

function trustedOrigin(origin, own) {
  if (origin === undefined || origin === 'null' || origin === own) return true;
  return EXTENSION_ORIGINS.some(scheme => origin.startsWith(scheme));
}

async function readJsonBody(request) {
  let body = '';
  for await (const chunk of request) {
    body += chunk;
    if (body.length > MAX_POST_BODY) throw new RangeError('Request too large');
  }
  const parsed = JSON.parse(body);
  if (!parsed || typeof parsed !== 'object') throw new TypeError('Object required');
  return parsed;
}

async function handleControl(request, { control }) {
  try {
    const body = await readJsonBody(request);
    const { action } = body;
    if (!CONTROL_ACTIONS.includes(action)) return [400, { error: 'Unknown action' }];
    const argumentsSpec = controlArguments[action];
    const fields = argumentsSpec ? argumentsSpec.parse(body) : {};
    if (!fields) return [400, { error: argumentsSpec.error }];
    return [200, { action, done: Boolean(await control(action, fields)) }];
  } catch (error) {
    return error instanceof RangeError ? [413, { error: error.message }] : [400, { error: 'Invalid control request' }];
  }
}

// Casting is accepted, not awaited: a load can take tens of seconds and the
// sender watches /api/status for the outcome.
async function handleCast(request, { cast }) {
  let body;
  try { body = await readJsonBody(request); } catch (error) {
    return error instanceof RangeError ? [413, { error: error.message }] : [400, { error: 'Invalid cast request' }];
  }
  const position = body.position === undefined ? 0 : Number(body.position);
  if (!isVideoId(body.videoId)) return [400, { error: 'Invalid videoId' }];
  if (!Number.isFinite(position) || position < 0) return [400, { error: 'Invalid position' }];
  try {
    await cast(body.videoId, position);
    return [202, { videoId: body.videoId, position }];
  } catch { return [503, { error: 'Van TV is not ready to cast' }]; }
}

export async function listenStatus(options, { addresses = ['127.0.0.1', config.lanAddress],
  port = config.statusPort } = {}) {
  const servers = [];
  try {
    for (const host of new Set(addresses)) {
      const server = await createStatusServer(options);
      servers.push(server);
      await new Promise((resolve, reject) => {
        server.once('error', reject);
        server.listen(port, host, resolve);
      });
    }
    return () => Promise.all(servers.map(server => new Promise(resolve => {
      server.close(resolve);
      server.closeIdleConnections();
    })));
  } catch (error) {
    for (const server of servers) server.close();
    throw error;
  }
}
