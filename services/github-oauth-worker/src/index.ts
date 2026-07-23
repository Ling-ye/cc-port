const GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize";
const GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token";
const GITHUB_USER_URL = "https://api.github.com/user";
const SESSION_TTL_SECONDS = 600;
const POLL_INTERVAL_SECONDS = 2;
const MAX_BODY_BYTES = 4096;
const SESSION_ID_RE = /^[A-Za-z0-9_-]{32}$/;
const SECRET_RE = /^[A-Za-z0-9_-]{43}$/;
const STATE_NONCE_RE = /^[A-Za-z0-9_-]{32}$/;
const CODE_CHALLENGE_RE = /^[A-Za-z0-9_-]{43,128}$/;
const CODE_VERIFIER_RE = /^[A-Za-z0-9._~-]{43,128}$/;

type Purpose = "standard" | "organization_owner" | "remote_delete";
type SessionStatus = "pending" | "ready" | "denied";

const PURPOSE_SCOPES: Record<Purpose, readonly string[]> = {
  standard: ["repo"],
  organization_owner: ["repo", "read:org"],
  remote_delete: ["repo", "delete_repo"],
};
const ALLOWED_SCOPES = new Set(["repo", "read:org", "delete_repo"]);

interface DurableObjectStorageLike {
  get<T>(key: string): Promise<T | undefined>;
  put<T>(key: string, value: T): Promise<void>;
  deleteAll(): Promise<void>;
  setAlarm(timestamp: number): Promise<void>;
}

interface DurableObjectStateLike {
  storage: DurableObjectStorageLike;
}

interface DurableObjectStubLike {
  fetch(request: Request): Promise<Response>;
}

interface DurableObjectNamespaceLike {
  idFromName(name: string): unknown;
  get(id: unknown): DurableObjectStubLike;
}

interface RateLimitLike {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  OAUTH_SESSIONS: DurableObjectNamespaceLike;
  SESSION_START_RATE_LIMIT: RateLimitLike;
  GITHUB_OAUTH_CLIENT_ID: string;
  GITHUB_OAUTH_CLIENT_SECRET: string;
}

interface StoredSession {
  purpose: Purpose;
  scopes: string[];
  pollTokenHash: string;
  stateNonceHash: string;
  codeChallenge: string;
  callbackUrl: string;
  expiresAt: number;
  nextPollAt: number;
  status: SessionStatus;
  code: string;
}

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    return handleRequest(request, env);
  },
};

export async function handleRequest(request: Request, env: Env): Promise<Response> {
  try {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true });
    }
    if (request.method === "POST" && url.pathname === "/v1/oauth/sessions") {
      return createSession(request, env, url.origin);
    }
    if (request.method === "GET" && url.pathname === "/oauth/callback") {
      return oauthCallback(url, env);
    }

    const match = url.pathname.match(
      /^\/v1\/oauth\/sessions\/([A-Za-z0-9_-]{32})(?:\/poll)?$/,
    );
    if (match && request.method === "POST" && url.pathname.endsWith("/poll")) {
      return forwardAuthenticated(request, env, match[1], "/poll");
    }
    if (match && request.method === "DELETE" && !url.pathname.endsWith("/poll")) {
      return forwardAuthenticated(request, env, match[1], "/cancel");
    }
    return json({ error: "not_found" }, 404);
  } catch (error) {
    if (error instanceof PublicError) {
      return json({ error: error.code }, error.status);
    }
    return json({ error: "oauth_broker_unavailable" }, 500);
  }
}

async function createSession(request: Request, env: Env, origin: string): Promise<Response> {
  requireJson(request);
  const rateKey = request.headers.get("CF-Connecting-IP") || "unknown";
  const rate = await env.SESSION_START_RATE_LIMIT.limit({ key: `start:${rateKey}` });
  if (!rate.success) {
    return json({ error: "rate_limited" }, 429, { "Retry-After": "60" });
  }

  const body = await readJson(request);
  const purpose = parsePurpose(body.purpose);
  const codeChallenge = String(body.code_challenge || "");
  if (!CODE_CHALLENGE_RE.test(codeChallenge)) {
    throw new PublicError(400, "invalid_code_challenge");
  }
  const scopes = [...PURPOSE_SCOPES[purpose]];
  const sessionId = randomToken(24);
  const pollToken = randomToken(32);
  const stateNonce = randomToken(24);
  const callbackUrl = `${origin}/oauth/callback`;
  const stateValue = `${sessionId}.${stateNonce}`;
  const now = Date.now();
  const stored: StoredSession = {
    purpose,
    scopes,
    pollTokenHash: await sha256(pollToken),
    stateNonceHash: await sha256(stateNonce),
    codeChallenge,
    callbackUrl,
    expiresAt: now + SESSION_TTL_SECONDS * 1000,
    nextPollAt: now,
    status: "pending",
    code: "",
  };
  const stub = sessionStub(env, sessionId);
  await stub.fetch(internalRequest("/create", stored));

  const authorizationUrl = new URL(GITHUB_AUTHORIZE_URL);
  authorizationUrl.searchParams.set("client_id", requireSecret(env.GITHUB_OAUTH_CLIENT_ID));
  authorizationUrl.searchParams.set("redirect_uri", callbackUrl);
  authorizationUrl.searchParams.set("scope", scopes.join(" "));
  authorizationUrl.searchParams.set("state", stateValue);
  authorizationUrl.searchParams.set("code_challenge", codeChallenge);
  authorizationUrl.searchParams.set("code_challenge_method", "S256");

  return json({
    session_id: sessionId,
    poll_token: pollToken,
    authorization_url: authorizationUrl.toString(),
    expires_in: SESSION_TTL_SECONDS,
    interval: POLL_INTERVAL_SECONDS,
    purpose,
    scopes,
  });
}

async function oauthCallback(url: URL, env: Env): Promise<Response> {
  const rawState = url.searchParams.get("state") || "";
  const separator = rawState.indexOf(".");
  if (separator <= 0) {
    return callbackPage("", "invalid");
  }
  const sessionId = rawState.slice(0, separator);
  const stateNonce = rawState.slice(separator + 1);
  if (!SESSION_ID_RE.test(sessionId) || !STATE_NONCE_RE.test(stateNonce)) {
    return callbackPage("", "invalid");
  }
  const code = url.searchParams.get("code") || "";
  const oauthError = url.searchParams.get("error") || "";
  const response = await sessionStub(env, sessionId).fetch(
    internalRequest("/callback", {
      state_nonce: stateNonce,
      code,
      error: oauthError,
    }),
  );
  const result = await safeJson(response);
  const status = response.ok
    ? (result.state === "denied" ? "denied" : "success")
    : "invalid";
  return callbackPage(response.ok ? sessionId : "", status);
}

async function forwardAuthenticated(
  request: Request,
  env: Env,
  sessionId: string,
  target: "/poll" | "/cancel",
): Promise<Response> {
  const token = bearerToken(request);
  if (!SECRET_RE.test(token)) {
    throw new PublicError(401, "invalid_session");
  }
  let body: Record<string, unknown> = {};
  if (target === "/poll") {
    requireJson(request);
    body = await readJson(request);
  }
  return sessionStub(env, sessionId).fetch(
    internalRequest(target, body, { Authorization: `Bearer ${token}` }),
  );
}

export class OAuthSession {
  constructor(
    private readonly state: DurableObjectStateLike,
    private readonly env: Env,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const path = new URL(request.url).pathname;
    if (path === "/create" && request.method === "POST") {
      const session = await readJson(request) as unknown as StoredSession;
      await this.state.storage.put("session", session);
      await this.state.storage.setAlarm(session.expiresAt);
      return json({ created: true }, 201);
    }

    const session = await this.state.storage.get<StoredSession>("session");
    if (!session || Date.now() >= session.expiresAt) {
      await this.state.storage.deleteAll();
      return json({ state: "expired" }, 410);
    }
    if (path === "/callback" && request.method === "POST") {
      return this.callback(session, await readJson(request));
    }
    if (path === "/poll" && request.method === "POST") {
      return this.poll(session, request, await readJson(request));
    }
    if (path === "/cancel" && request.method === "POST") {
      if (!(await this.authorized(session, request))) {
        return json({ error: "invalid_session" }, 401);
      }
      await this.state.storage.deleteAll();
      return json({ cancelled: true });
    }
    return json({ error: "not_found" }, 404);
  }

  async alarm(): Promise<void> {
    await this.state.storage.deleteAll();
  }

  private async callback(
    session: StoredSession,
    body: Record<string, unknown>,
  ): Promise<Response> {
    const stateNonce = String(body.state_nonce || "");
    if (!STATE_NONCE_RE.test(stateNonce)
      || !constantTimeEqual(await sha256(stateNonce), session.stateNonceHash)) {
      return json({ error: "invalid_state" }, 400);
    }
    const oauthError = String(body.error || "");
    if (oauthError) {
      session.status = "denied";
      session.code = "";
      await this.state.storage.put("session", session);
      return json({ state: "denied" });
    }
    const code = String(body.code || "");
    if (!code || code.length > 512) {
      return json({ error: "invalid_code" }, 400);
    }
    session.status = "ready";
    session.code = code;
    await this.state.storage.put("session", session);
    return json({ state: "ready" });
  }

  private async poll(
    session: StoredSession,
    request: Request,
    body: Record<string, unknown>,
  ): Promise<Response> {
    if (!(await this.authorized(session, request))) {
      return json({ error: "invalid_session" }, 401);
    }
    if (session.status === "denied") {
      await this.state.storage.deleteAll();
      return json({ state: "denied" });
    }
    const now = Date.now();
    if (session.status === "pending") {
      const retryAfter = Math.max(
        1,
        Math.ceil((session.nextPollAt - now) / 1000),
      );
      if (now < session.nextPollAt) {
        return json({ state: "pending", retry_after: retryAfter });
      }
      session.nextPollAt = now + POLL_INTERVAL_SECONDS * 1000;
      await this.state.storage.put("session", session);
      return json({ state: "pending", retry_after: POLL_INTERVAL_SECONDS });
    }

    const verifier = String(body.code_verifier || "");
    if (!CODE_VERIFIER_RE.test(verifier)
      || !constantTimeEqual(await pkceChallenge(verifier), session.codeChallenge)) {
      return json({ error: "invalid_code_verifier" }, 401);
    }
    const result = await exchangeCode(session, verifier, this.env);
    await this.state.storage.deleteAll();
    return json({ state: "authorized", ...result });
  }

  private async authorized(session: StoredSession, request: Request): Promise<boolean> {
    const token = bearerToken(request);
    return SECRET_RE.test(token)
      && constantTimeEqual(await sha256(token), session.pollTokenHash);
  }
}

async function exchangeCode(
  session: StoredSession,
  verifier: string,
  env: Env,
): Promise<{ access_token: string; login: string; scopes: string[] }> {
  const tokenResponse = await fetch(GITHUB_TOKEN_URL, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/x-www-form-urlencoded",
      "User-Agent": "LingyePluginMarketplace-OAuth-Broker",
    },
    body: new URLSearchParams({
      client_id: requireSecret(env.GITHUB_OAUTH_CLIENT_ID),
      client_secret: requireSecret(env.GITHUB_OAUTH_CLIENT_SECRET),
      code: session.code,
      redirect_uri: session.callbackUrl,
      code_verifier: verifier,
    }),
  });
  const tokenData = await safeJson(tokenResponse);
  const accessToken = String(tokenData.access_token || "");
  if (!tokenResponse.ok || !accessToken) {
    throw new PublicError(502, "github_token_exchange_failed");
  }

  const userResponse = await fetch(GITHUB_USER_URL, {
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${accessToken}`,
      "User-Agent": "LingyePluginMarketplace-OAuth-Broker",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  const userData = await safeJson(userResponse);
  const login = String(userData.login || "");
  const scopes = orderedScopes(
    (userResponse.headers.get("x-oauth-scopes") || "")
      .split(",")
      .map((scope) => scope.trim())
      .filter(Boolean),
  );
  if (!userResponse.ok || !login || session.scopes.some((scope) => !scopes.includes(scope))) {
    throw new PublicError(502, "github_token_validation_failed");
  }
  return { access_token: accessToken, login, scopes };
}

function sessionStub(env: Env, sessionId: string): DurableObjectStubLike {
  return env.OAUTH_SESSIONS.get(env.OAUTH_SESSIONS.idFromName(sessionId));
}

function callbackPage(sessionId: string, result: string): Response {
  const safeSession = SESSION_ID_RE.test(sessionId) ? sessionId : "";
  const scriptNonce = randomToken(16);
  const deepLink = safeSession
    ? `lingye-lpm://oauth/complete?session_id=${encodeURIComponent(safeSession)}&result=${encodeURIComponent(result)}`
    : "";
  const title = result === "success"
    ? "GitHub authorization complete"
    : result === "denied"
      ? "GitHub authorization was cancelled"
      : "GitHub authorization could not be completed";
  const link = deepLink
    ? `<a id="return" href="${deepLink}">Return to LPM Desktop</a>`
    : "";
  const script = deepLink
    ? `<script nonce="${scriptNonce}">window.location.replace(${JSON.stringify(deepLink)});</script>`
    : "";
  return new Response(
    `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${title}</title></head><body><main><h1>${title}</h1><p>You can safely return to LPM Desktop.</p>${link}</main>${script}</body></html>`,
    {
      status: result === "invalid" ? 400 : 200,
      headers: securityHeaders("text/html; charset=utf-8", scriptNonce),
    },
  );
}

function internalRequest(
  path: string,
  body: unknown,
  headers: Record<string, string> = {},
): Request {
  return new Request(`https://oauth-session.internal${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

function requireJson(request: Request): void {
  const contentType = request.headers.get("content-type") || "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new PublicError(415, "json_required");
  }
}

async function readJson(request: Request): Promise<Record<string, unknown>> {
  const declared = Number(request.headers.get("content-length") || "0");
  if (declared > MAX_BODY_BYTES) {
    throw new PublicError(413, "request_too_large");
  }
  const text = await request.text();
  if (new TextEncoder().encode(text).byteLength > MAX_BODY_BYTES) {
    throw new PublicError(413, "request_too_large");
  }
  try {
    const parsed: unknown = JSON.parse(text || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("not an object");
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new PublicError(400, "invalid_json");
  }
}

async function safeJson(response: Response): Promise<Record<string, unknown>> {
  try {
    const parsed: unknown = await response.json();
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function parsePurpose(value: unknown): Purpose {
  const purpose = String(value || "") as Purpose;
  if (!(purpose in PURPOSE_SCOPES)) {
    throw new PublicError(400, "invalid_purpose");
  }
  return purpose;
}

function bearerToken(request: Request): string {
  const value = request.headers.get("authorization") || "";
  return value.startsWith("Bearer ") ? value.slice(7).trim() : "";
}

function requireSecret(value: string): string {
  const clean = String(value || "").trim();
  if (!clean) {
    throw new PublicError(503, "oauth_not_configured");
  }
  return clean;
}

function orderedScopes(scopes: string[]): string[] {
  const values = new Set(scopes.filter((scope) => ALLOWED_SCOPES.has(scope)));
  return ["repo", "read:org", "delete_repo"].filter((scope) => values.has(scope));
}

function randomToken(bytes: number): string {
  const value = new Uint8Array(bytes);
  crypto.getRandomValues(value);
  return base64Url(value);
}

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

async function pkceChallenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function base64Url(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function securityHeaders(contentType: string, scriptNonce = ""): Headers {
  const scriptPolicy = scriptNonce ? `script-src 'nonce-${scriptNonce}'; ` : "";
  return new Headers({
    "Cache-Control": "no-store",
    "Content-Security-Policy": `default-src 'none'; ${scriptPolicy}base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
    "Content-Type": contentType,
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  });
}

function json(
  value: unknown,
  status = 200,
  extraHeaders: Record<string, string> = {},
): Response {
  const headers = securityHeaders("application/json; charset=utf-8");
  for (const [key, headerValue] of Object.entries(extraHeaders)) {
    headers.set(key, headerValue);
  }
  return new Response(JSON.stringify(value), { status, headers });
}

class PublicError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
  }
}
