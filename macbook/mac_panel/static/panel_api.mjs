// The only fetch boundary: per-instance token, JSON bodies, and token renewal after a panel restart.
let token = document.querySelector('meta[name="panel-token"]').content;

export async function renewToken() {
  const response = await fetch('/', {cache: 'no-store'});
  if (!response.ok) throw new Error('Panel is not responding');
  const page = new DOMParser().parseFromString(await response.text(), 'text/html');
  token = page.querySelector('meta[name="panel-token"]').content;
}

export async function api(path, body, retry = true) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
    headers: {'X-Panel-Token': token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (response.status === 403 && body === undefined && retry) {
    await renewToken();
    return api(path, undefined, false);
  }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return result;
}

export async function health() {
  const response = await fetch('/healthz', {cache: 'no-store'});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
