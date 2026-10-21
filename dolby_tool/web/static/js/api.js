/** Backend access: one JSON fetch wrapper with readable errors, plus a WebSocket opener. */

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/**
 * Turn an error body into one readable sentence. FastAPI sends either
 * {"detail": "msg"} or, for validation errors (422), {"detail": [{loc, msg}, …]}.
 */
function errorMessage(body, status) {
  const detail = body?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((item) => {
        const field = (item?.loc ?? []).filter((part) => part !== 'body' && part !== 'query').join('.');
        return field ? `${field}: ${item?.msg ?? 'invalid'}` : (item?.msg ?? String(item));
      })
      .join('; ');
  }
  return status ? `Request failed (HTTP ${status})` : 'Request failed';
}

/** Fetch JSON from the backend. Throws ApiError with a message fit for the UI. */
async function request(path, { method = 'GET', body, params } = {}) {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value != null && value !== '') url.searchParams.set(key, value);
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError('Can’t reach the dolby-tool server — is it still running?');
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text.trim().slice(0, 300) };
  }
  if (!response.ok) throw new ApiError(errorMessage(data, response.status), response.status);
  return data;
}

export const getJson = (path, params) => request(path, { params });
export const postJson = (path, body) => request(path, { method: 'POST', body });

/** Open a WebSocket to a backend path on the current host (ws:// or wss://). */
export function openSocket(path) {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return new WebSocket(`${protocol}//${location.host}${path}`);
}
