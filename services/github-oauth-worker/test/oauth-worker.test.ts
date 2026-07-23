import { afterEach, describe, expect, it, vi } from "vitest";
import {
  handleRequest,
  OAuthSession,
  type Env,
} from "../src/index";

class FakeStorage {
  values = new Map<string, unknown>();
  alarmAt = 0;

  async get<T>(key: string): Promise<T | undefined> {
    return this.values.get(key) as T | undefined;
  }

  async put<T>(key: string, value: T): Promise<void> {
    this.values.set(key, structuredClone(value));
  }

  async deleteAll(): Promise<void> {
    this.values.clear();
    this.alarmAt = 0;
  }

  async setAlarm(timestamp: number): Promise<void> {
    this.alarmAt = timestamp;
  }
}

class FakeNamespace {
  private readonly objects = new Map<string, OAuthSession>();
  private readonly storages = new Map<string, FakeStorage>();
  private env!: Env;

  setEnv(env: Env): void {
    this.env = env;
  }

  idFromName(name: string): unknown {
    return name;
  }

  get(id: unknown): { fetch(request: Request): Promise<Response> } {
    const name = String(id);
    let instance = this.objects.get(name);
    if (!instance) {
      const storage = new FakeStorage();
      this.storages.set(name, storage);
      instance = new OAuthSession({ storage }, this.env);
      this.objects.set(name, instance);
    }
    return { fetch: (request) => instance!.fetch(request) };
  }

  storage(name: string): FakeStorage | undefined {
    return this.storages.get(name);
  }

  instance(name: string): OAuthSession | undefined {
    return this.objects.get(name);
  }
}

function environment(rateAllowed = true): Env {
  const namespace = new FakeNamespace();
  const env: Env = {
    OAUTH_SESSIONS: namespace,
    SESSION_START_RATE_LIMIT: {
      limit: vi.fn(async () => ({ success: rateAllowed })),
    },
    GITHUB_OAUTH_CLIENT_ID: "client-id",
    GITHUB_OAUTH_CLIENT_SECRET: "client-secret",
  };
  namespace.setEnv(env);
  return env;
}

async function challenge(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  const bytes = new Uint8Array(digest);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

async function startSession(
  env: Env,
  verifier = "a".repeat(64),
  purpose = "standard",
): Promise<Record<string, unknown>> {
  const response = await handleRequest(
    new Request("https://oauth.example/v1/oauth/sessions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "CF-Connecting-IP": "203.0.113.1",
      },
      body: JSON.stringify({
        purpose,
        code_challenge: await challenge(verifier),
      }),
    }),
    env,
  );
  expect(response.status).toBe(200);
  return response.json() as Promise<Record<string, unknown>>;
}

function authenticatedRequest(
  url: string,
  token: string,
  body: Record<string, unknown>,
): Request {
  return new Request(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

async function completeCallback(
  env: Env,
  session: Record<string, unknown>,
  extra: Record<string, string> = { code: "github-code" },
): Promise<Response> {
  const authorization = new URL(String(session.authorization_url));
  const callback = new URL("https://oauth.example/oauth/callback");
  callback.searchParams.set("state", authorization.searchParams.get("state") || "");
  for (const [key, value] of Object.entries(extra)) callback.searchParams.set(key, value);
  return handleRequest(new Request(callback), env);
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GitHub OAuth broker", () => {
  it("creates a PKCE session with fixed scopes and no credential in the authorization URL", async () => {
    const env = environment();
    const session = await startSession(env);
    const authorization = new URL(String(session.authorization_url));

    expect(session.session_id).toMatch(/^[A-Za-z0-9_-]{32}$/);
    expect(session.poll_token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(session.scopes).toEqual(["repo"]);
    expect(authorization.origin + authorization.pathname)
      .toBe("https://github.com/login/oauth/authorize");
    expect(authorization.searchParams.get("client_id")).toBe("client-id");
    expect(authorization.searchParams.get("code_challenge_method")).toBe("S256");
    expect(authorization.toString()).not.toContain("client-secret");
  });

  it("stores the callback result, exchanges once, and returns the validated account", async () => {
    const env = environment();
    const verifier = "b".repeat(64);
    const session = await startSession(env, verifier);
    const callbackResponse = await completeCallback(env, session);
    const callbackHtml = await callbackResponse.text();

    expect(callbackResponse.status).toBe(200);
    expect(callbackHtml).toContain("lingye-lpm://oauth/complete");
    expect(callbackHtml).not.toContain("github-code");
    expect(callbackResponse.headers.get("Content-Security-Policy"))
      .toContain("script-src 'nonce-");
    expect(callbackResponse.headers.get("Content-Security-Policy"))
      .not.toContain("'unsafe-inline'");

    const githubFetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/login/oauth/access_token")) {
        return new Response(JSON.stringify({ access_token: "github-token" }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ login: "Lingye" }), {
        headers: {
          "Content-Type": "application/json",
          "X-OAuth-Scopes": "repo",
        },
      });
    });
    vi.stubGlobal("fetch", githubFetch);

    const pollResponse = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: verifier },
      ),
      env,
    );
    const result = await pollResponse.json() as Record<string, unknown>;

    expect(result).toEqual({
      state: "authorized",
      access_token: "github-token",
      login: "Lingye",
      scopes: ["repo"],
    });
    expect(githubFetch).toHaveBeenCalledTimes(2);

    const repeated = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: verifier },
      ),
      env,
    );
    expect(repeated.status).toBe(410);
  });

  it("rejects forged state without exposing the supplied authorization code", async () => {
    const env = environment();
    const session = await startSession(env);
    const callback = new URL("https://oauth.example/oauth/callback");
    callback.searchParams.set("state", `${session.session_id}.${"x".repeat(32)}`);
    callback.searchParams.set("code", "sensitive-code");

    const response = await handleRequest(new Request(callback), env);
    const html = await response.text();

    expect(response.status).toBe(400);
    expect(html).not.toContain("sensitive-code");
    expect(html).not.toContain("lingye-lpm://oauth/complete");
  });

  it("reports denial and deletes the session without exchanging a token", async () => {
    const env = environment();
    const session = await startSession(env);
    const callback = await completeCallback(env, session, { error: "access_denied" });
    expect(await callback.text()).toContain("authorization was cancelled");

    const githubFetch = vi.fn();
    vi.stubGlobal("fetch", githubFetch);
    const response = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: "a".repeat(64) },
      ),
      env,
    );

    expect(await response.json()).toEqual({ state: "denied" });
    expect(githubFetch).not.toHaveBeenCalled();
  });

  it("rejects an invalid verifier and keeps the authorization code unexposed", async () => {
    const env = environment();
    const session = await startSession(env, "c".repeat(64));
    await completeCallback(env, session);

    const response = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: "d".repeat(64) },
      ),
      env,
    );
    const text = await response.text();

    expect(response.status).toBe(401);
    expect(text).toContain("invalid_code_verifier");
    expect(text).not.toContain("github-code");
  });

  it("cancels an authenticated session and expires it immediately", async () => {
    const env = environment();
    const session = await startSession(env);
    const response = await handleRequest(
      new Request(`https://oauth.example/v1/oauth/sessions/${session.session_id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${session.poll_token}` },
      }),
      env,
    );
    expect(await response.json()).toEqual({ cancelled: true });

    const expired = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: "a".repeat(64) },
      ),
      env,
    );
    expect(expired.status).toBe(410);
  });

  it("expires a session after ten minutes without exchanging a token", async () => {
    const env = environment();
    const session = await startSession(env);
    const namespace = env.OAUTH_SESSIONS as FakeNamespace;
    const storage = namespace.storage(String(session.session_id));
    const stored = await storage?.get<Record<string, unknown>>("session");
    expect(stored).toBeDefined();
    await storage?.put("session", {
      ...stored,
      expiresAt: Date.now() - 1,
    });

    const githubFetch = vi.fn();
    vi.stubGlobal("fetch", githubFetch);
    const response = await handleRequest(
      authenticatedRequest(
        `https://oauth.example/v1/oauth/sessions/${session.session_id}/poll`,
        String(session.poll_token),
        { code_verifier: "a".repeat(64) },
      ),
      env,
    );

    expect(response.status).toBe(410);
    expect(await response.json()).toEqual({ state: "expired" });
    expect(githubFetch).not.toHaveBeenCalled();
  });

  it("deletes Durable Object state when its expiration alarm fires", async () => {
    const env = environment();
    const session = await startSession(env);
    const namespace = env.OAUTH_SESSIONS as FakeNamespace;
    const storage = namespace.storage(String(session.session_id));
    expect(storage?.alarmAt).toBeGreaterThan(Date.now());

    await namespace.instance(String(session.session_id))?.alarm();

    expect(storage?.values.size).toBe(0);
    expect(storage?.alarmAt).toBe(0);
  });

  it("rate limits session creation before allocating Durable Object state", async () => {
    const env = environment(false);
    const response = await handleRequest(
      new Request("https://oauth.example/v1/oauth/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purpose: "standard",
          code_challenge: await challenge("e".repeat(64)),
        }),
      }),
      env,
    );

    expect(response.status).toBe(429);
    expect(response.headers.get("Retry-After")).toBe("60");
  });
});
