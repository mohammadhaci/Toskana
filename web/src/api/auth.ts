/** API token handling (optional server-side auth, M12).
 *
 * The token lives in localStorage; `request()` sends it as a Bearer header
 * and media URLs (`<img>`/`<a>`/WebSocket cannot set headers) append it as
 * `?token=`. A 401 from the server notifies subscribers so the app can show
 * the token prompt. No token stored + no 401 = open LAN mode, no UI change.
 */

const STORAGE_KEY = "toskana.apiToken";

type Listener = () => void;

const listeners = new Set<Listener>();

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token === null || token === "") window.localStorage.removeItem(STORAGE_KEY);
    else window.localStorage.setItem(STORAGE_KEY, token);
  } catch {
    // storage unavailable (private mode): the token just won't persist
  }
}

/** Headers for fetch(): the Bearer token when one is stored. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Append `?token=` to a media URL (for <img>/<a>/WS, which cannot set headers). */
export function withToken(url: string): string {
  const token = getToken();
  if (!token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/** Subscribe to 401 notifications; returns an unsubscribe function. */
export function onUnauthorized(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function notifyUnauthorized(): void {
  for (const listener of listeners) listener();
}
