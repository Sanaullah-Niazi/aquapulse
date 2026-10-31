// Talking to the AquaPulse API.
//
// Logging in gives two tokens (FR-69):
// * the access token (15 minutes) is kept only in memory, never on disk;
// * the refresh token (7 days, works once) is kept in localStorage, so a
//   reload keeps you logged in. When the access token runs out, we swap the
//   refresh token for new ones and repeat the request, without you noticing.
import { API_BASE } from "./config";

const REFRESH_KEY = "aquapulse.refresh";

export interface Tokens {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

/** An answer from the API that was not OK, with its message for people. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public headers: Headers = new Headers(),
  ) {
    super(message);
  }
}

let accessToken: string | null = null;
let onLoggedOut: () => void = () => {};

export function setLoggedOutHandler(handler: () => void): void {
  onLoggedOut = handler;
}

export function saveTokens(tokens: Tokens): void {
  accessToken = tokens.access_token;
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function forgetTokens(): void {
  accessToken = null;
  localStorage.removeItem(REFRESH_KEY);
}

export function hasRefreshToken(): boolean {
  return localStorage.getItem(REFRESH_KEY) !== null;
}

/** FastAPI answers {"detail": "text"} or, for bad input, a list of problems. */
async function errorFrom(response: Response): Promise<ApiError> {
  let message = response.statusText || `Error ${response.status}`;
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
    else if (Array.isArray(body.detail))
      message = body.detail
        .map((d: { loc?: string[]; msg: string }) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg}`)
        .join("; ");
  } catch {
    // not JSON: keep the status text
  }
  if (response.status === 429) message = "Too many tries. Please wait a few minutes.";
  return new ApiError(response.status, message, response.headers);
}

let refreshing: Promise<boolean> | null = null;

/** Swap the refresh token for new tokens. One at a time, even across tabs:
 * a refresh token used twice ends the session (stolen-token protection). */
export function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const run = async () => {
      const token = localStorage.getItem(REFRESH_KEY); // maybe renewed by another tab
      if (!token) return false;
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: token }),
      });
      if (!response.ok) {
        if (localStorage.getItem(REFRESH_KEY) === token) forgetTokens();
        return false;
      }
      saveTokens(await response.json());
      return true;
    };
    try {
      return navigator.locks ? await navigator.locks.request("aquapulse-refresh", run) : await run();
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

interface Options {
  method?: string;
  body?: unknown;
  /** false for login, password reset and other calls made before logging in. */
  auth?: boolean;
}

async function send(path: string, { method = "GET", body, auth = true }: Options): Promise<Response> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
  return fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Call the API and return its JSON answer (or throw an ApiError). */
export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const response = await raw(path, options);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** Like api(), but returns the Response (for files such as PDF bills). */
export async function raw(path: string, options: Options = {}): Promise<Response> {
  const auth = options.auth ?? true;
  if (auth && !accessToken && hasRefreshToken()) await refreshTokens();
  let response = await send(path, options);
  if (response.status === 401 && auth && hasRefreshToken() && (await refreshTokens())) {
    response = await send(path, options); // the access token had run out
  }
  if (response.status === 401 && auth) {
    forgetTokens();
    onLoggedOut();
  }
  if (!response.ok) throw await errorFrom(response);
  return response;
}

/** Download a file the API makes (a PDF bill) with the right file name. */
export async function download(path: string, fallbackName: string): Promise<void> {
  const response = await raw(path);
  const header = response.headers.get("Content-Disposition") ?? "";
  const name = /filename="?([^";]+)"?/.exec(header)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
