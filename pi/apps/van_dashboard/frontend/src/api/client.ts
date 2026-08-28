export class ApiRequestError extends Error {
  readonly status: number;
  readonly payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = status;
    this.payload = payload;
  }
}

type FormValue = string | number;

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiRequestError(
      `The dashboard returned HTTP ${response.status} without JSON`,
      response.status,
      null,
    );
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (
    typeof payload === 'object' &&
    payload !== null &&
    'message' in payload &&
    typeof payload.message === 'string'
  ) {
    return payload.message;
  }
  return `Dashboard request failed with HTTP ${status}`;
}

async function requestJson(path: string, init?: RequestInit): Promise<unknown> {
  if (!path.startsWith('/api/')) {
    throw new Error(`Dashboard API paths must start with /api/: ${path}`);
  }

  const response = await fetch(path, {
    cache: 'no-store',
    ...init,
  });
  const payload = await readJson(response);
  const payloadReportsFailure =
    typeof payload === 'object' && payload !== null && 'ok' in payload && payload.ok === false;

  if (!response.ok || payloadReportsFailure) {
    throw new ApiRequestError(errorMessage(payload, response.status), response.status, payload);
  }
  return payload;
}

export function getJson(path: string, signal?: AbortSignal): Promise<unknown> {
  return requestJson(path, { signal });
}

export function postForm(
  path: string,
  values: Readonly<Record<string, FormValue>> = {},
  signal?: AbortSignal,
): Promise<unknown> {
  const body = new URLSearchParams();
  for (const [name, value] of Object.entries(values)) {
    body.set(name, String(value));
  }

  return requestJson(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Van-Dashboard': '1',
    },
    body,
    signal,
  });
}
