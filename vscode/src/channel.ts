// The editor channel's client (sidecar/fabric_desktop/editor_channel.py, docs/vscode.md): how this
// window pairs with KratosForge and runs cells in a notebook's session there. No VS Code API here,
// so it's tested against the real engine without VS Code.

import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";

export interface Snapshot {
  status: string; // running, ok, error, idle, paused
  execution_count?: number;
  version?: number;
  seconds?: number;
  outputs?: Array<Record<string, unknown>>;
}

export class ChannelError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string) {
    super(message);
  }
}

/** KratosForge's data folder, where the app writes editor-port. */
export function dataDir(env: NodeJS.ProcessEnv = process.env): string {
  return path.join(env.LOCALAPPDATA || path.join(env.HOME || env.USERPROFILE || ".", ".local", "share"), "FabricDesktop");
}

/** The port and hello the app wrote while "Let VS Code run notebooks" is on; null while it's off. */
export function readPortFile(dir: string): { port: number; hello: string } | null {
  let text: string;
  try {
    text = fs.readFileSync(path.join(dir, "editor-port"), "utf8");
  } catch {
    return null;
  }
  if (text.length > 1024) return null;
  const [port, hello, ...rest] = text.trim().split(/[ \t\r\n]+/);
  const n = Number(port);
  // The app writes "<port> <hello>", with hello from secrets.token_urlsafe: anything else isn't its
  // file, and a hello that isn't one is never sent as a header.
  return /^[0-9]{1,5}$/.test(port) && n > 0 && n < 65536 && HELLO.test(hello || "") && !rest.length ? { port: n, hello } : null;
}

const HELLO = /^[A-Za-z0-9_-]{1,256}$/;
// A pairing token as the app issues it (editor_pairing.py: "kfp_" and secrets.token_urlsafe(32)).
const TOKEN = /^[A-Za-z0-9_-]{16,512}$/;
const KEEPS = ["session", "8h", "7d"];

/** ``value`` when ``ok`` says it's shaped as the caller reads it; otherwise the call fails as a reply
 * this extension can't read, before anything acts on it. */
function shaped<T>(value: unknown, ok: (v: any) => boolean): T {
  let fine = false;
  try {
    fine = !!value && typeof value === "object" && !Array.isArray(value) && ok(value);
  } catch {
    fine = false;
  }
  if (!fine) throw new ChannelError(0, "bad_reply", BAD_REPLY);
  return value as T;
}
const text = (v: unknown, max = 512): boolean => typeof v === "string" && v.length > 0 && v.length <= max;
const count = (v: unknown): boolean => v === undefined || v === null || (Number.isSafeInteger(v) && (v as number) >= 0);

/** A cell's snapshot: a status, and outputs that are each an object (outputs.ts reads each one). */
function snapshot(value: unknown): Snapshot {
  const snap = shaped<Snapshot>(value, (v) => text(v.status, 40)
    && (v.outputs === undefined || v.outputs === null || (Array.isArray(v.outputs)
      && v.outputs.every((o: unknown) => !!o && typeof o === "object" && !Array.isArray(o))))
    && count(v.version) && count(v.execution_count)
    && (v.seconds === undefined || v.seconds === null || (Number.isFinite(v.seconds) && v.seconds >= 0)));
  for (const k of ["outputs", "version", "execution_count", "seconds"] as const) if (snap[k] === null) delete snap[k];
  return snap;
}

export const NOT_LISTENING = "KratosForge isn't running, or Let VS Code run notebooks is off in its Settings > Integrations. "
  + "Start KratosForge and turn it on there, then run the cell again.";
export const NOT_KRATOSFORGE = "Something other than KratosForge is listening on the editor channel's port, so this window "
  + "sent it nothing. Restart KratosForge, then try again.";

/** The editor channel API this extension speaks (editor_channel.API_VERSION). An app that reports a
 * newer one has changed its routes, so this window stops before it misreads a reply. */
export const API_VERSION = 1;
export const CONNECTION_LOST = "KratosForge closed the connection, so it may have restarted or stopped. Run the cell again.";
export const RESTARTED = "The notebook's session in KratosForge ended while this cell ran (KratosForge may have restarted). Run the cell again.";
export const TIMED_OUT = "KratosForge didn't answer in time. It may be busy with a long task; try again in a moment, or restart KratosForge.";
export function unreachable(code: string): string {
  return `This window couldn't reach KratosForge on this PC (${code}). Try again; if it keeps happening, restart KratosForge.`;
}
// How often a dropped or slow poll of a running cell is tried again before the cell is reported as
// failed. Polling only reads, so asking again can't run anything twice; starting a cell is never retried.
const POLL_RETRIES = 3;
// A poll that got no answer at all is asked again only once, so a hung app fails the cell in about two minutes.
const POLL_TIMEOUT_RETRIES = 1;
export const NOT_PAIRED = "This window's pairing with KratosForge has ended. It ran out, was ended in KratosForge, or "
  + "KratosForge restarted. Run the cell again to pair this window.";
export const HELLO_CHANGED = "KratosForge restarted while this window was asking to pair. Run the cell again to ask again.";
export const OFF = "Let VS Code run notebooks is off in KratosForge's Settings > Integrations, or your organization doesn't "
  + "allow it. Turn it on there, or ask your admin if your organization turned it off, then run the cell again.";
export const PAIR_DENIED = "Pairing was denied in KratosForge. To pair this window, run the cell again and choose Pair this "
  + "window in KratosForge.";
export const PAIR_UNANSWERED = "Nobody answered in KratosForge, so this window isn't paired. Run the cell again, then choose "
  + "Pair this window in KratosForge while it asks.";
export const PAIR_CANCELLED = "Pairing was cancelled. Run the cell again to pair this window.";
export const PAIR_EXPIRED = "That pairing request ran out before anyone answered in KratosForge. Run the cell again to ask again.";
export const BAD_REPLY = "KratosForge sent a reply this extension can't read. Install kratosforge.vsix from KratosForge's "
  + "latest release, then reload this window.";
export const OUT_OF_STEP = " This extension and KratosForge may be out of step: install kratosforge.vsix from KratosForge's "
  + "latest release, then reload this window.";
export function unexpected(message: string): string {
  return `KratosForge hit an error it didn't expect (${message}). Run the cell again; if it keeps happening, restart KratosForge.`;
}

// Words that tell the person what to do next. An app message without any gets the window's own.
const NEXT_STEP = /run the cell again|install kratosforge|turn it on|pair the window|answer them|open the repository|restart kratosforge|try again|split it|open the notebook|pick kratosforge|start kratosforge|import the notebook|save the notebook|move it|rename/i;
export const hasNextStep = (text: string): boolean => NEXT_STEP.test(text);
const THEN_AGAIN = " Once that's sorted out, run the cell again.";

/** The error the window shows for an answer the app refused: each with a name and a next step. Where the
 * app's own words already say what to do (a notebook outside the paired folder, too many windows asking
 * to pair), they're kept; elsewhere the window says it in its own words. */
export function refusal(status: number, code: string | undefined, message: string | undefined, pairing = false,
  hello = false): ChannelError {
  // Only words and a name the app could have sent: anything else reads as no message and no code.
  const said = typeof message === "string" && message.length <= 4000 ? message.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").trim() : "";
  if (typeof code !== "string" || !/^[a-z_]{1,40}$/.test(code)) code = undefined;
  switch (code) {
    case "not_paired": return new ChannelError(status, "not_paired", NOT_PAIRED);
    // Only a call that sends the hello (asking to pair) is checked against it; anywhere else it means
    // the window's pairing no longer holds.
    case "unauthorized": return new ChannelError(status, "unauthorized", hello ? HELLO_CHANGED : NOT_PAIRED);
    case "off": return new ChannelError(status, "off", OFF);
    case "busy": return new ChannelError(status, "busy", BUSY);
    case "extension_too_old": return new ChannelError(status, "extension_too_old", EXTENSION_TOO_OLD);
    case "not_found": return pairing ? new ChannelError(status, "expired", PAIR_EXPIRED)
      : new ChannelError(status, "not_found", said ? (hasNextStep(said) ? said : said + THEN_AGAIN) : "KratosForge couldn't find that notebook or cell." + OUT_OF_STEP);
    case "invalid_input": case "too_large": return new ChannelError(status, code, (said ? `${said}` : "KratosForge refused the request.") + OUT_OF_STEP);
    case "internal": return new ChannelError(status, "internal", unexpected(said || `status ${status}`));
    case "refused": case "failed": if (said) return new ChannelError(status, code, hasNextStep(said) ? said : said + THEN_AGAIN);
      return new ChannelError(status, code, unexpected(`status ${status}`));
    default: return new ChannelError(status, code || "failed", said ? said + OUT_OF_STEP : unexpected(`status ${status}`));
  }
}

export const BUSY = "KratosForge has too many editor connections open right now. Try again in a moment.";
// Only the engine's accept-time refusal is "busy" (editor_channel.py), so a retried call never ran.
const BUSY_RETRIES = 3;

export const APP_TOO_NEW = "This KratosForge is newer than this extension. Install kratosforge.vsix from "
  + "KratosForge's latest release, then reload this window.";
export const EXTENSION_TOO_OLD = "This extension is too old for this KratosForge. Install kratosforge.vsix from "
  + "KratosForge's latest release, then reload this window.";

// The most one answer from the app may hold: a cell's outputs are capped in the app at about 17 MB of
// text and HTML, and errors on top. A longer answer is cut off and the call fails, rather than held.
export const MAX_REPLY = 64 * 1024 * 1024;
export const TOO_LARGE = "KratosForge's answer was larger than this extension accepts. Open the notebook in KratosForge to see this cell's output.";

// The most one request to the app may hold (MAX_BODY_BYTES in editor_channel.py). A cell past it is
// refused here, before anything is sent, with words that say it's the cell.
export const MAX_REQUEST = 1024 * 1024;

export function cellTooLarge(size: number): string {
  return `This cell is ${(Math.ceil(size / 1024 / 1024 * 10) / 10).toFixed(1)} MB, and KratosForge takes cells of up to 1 MB from VS Code. `
    + "Split it into smaller cells, or run it in KratosForge.";
}

interface Reply { status: number; payload: { ok?: boolean; result?: unknown; error?: { code?: string; message?: string } } | null }

function send(port: number, method: string, url: string, headers: Record<string, string | number>, data?: Buffer,
  timeout = 60_000): Promise<Reply> {
  return new Promise((resolve, reject) => {
    let timedOut = false;
    // A connection that drops or stalls after the answer has begun is reported on the response, not the request.
    const cut = () => reject(timedOut ? new ChannelError(0, "timed_out", TIMED_OUT) : new ChannelError(0, "connection_lost", CONNECTION_LOST));
    const req = http.request({ host: "127.0.0.1", port, method, path: url, headers: { Host: `127.0.0.1:${port}`, ...headers },
      timeout }, (res) => {
      if (Number(res.headers["content-length"]) > MAX_REPLY) {
        req.destroy();
        reject(new ChannelError(0, "too_large", TOO_LARGE));
        return;
      }
      res.on("aborted", cut);
      res.on("error", cut);
      res.on("close", () => { if (!res.complete) cut(); });
      const chunks: Buffer[] = [];
      let size = 0;
      res.on("data", (c: Buffer) => {
        size += c.length;
        if (size > MAX_REPLY) {
          req.destroy();
          reject(new ChannelError(0, "too_large", TOO_LARGE));
          return;
        }
        chunks.push(c);
      });
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode || 0, payload: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
        } catch {
          resolve({ status: res.statusCode || 0, payload: null });
        }
      });
    });
    req.on("timeout", () => {
      timedOut = true;
      req.destroy();
    });
    // Every way the connection can fail ends as a ChannelError in plain words, never a raw socket error.
    req.on("error", (err: NodeJS.ErrnoException) => {
      if (timedOut) reject(new ChannelError(0, "timed_out", TIMED_OUT));
      else if (err.code === "ECONNREFUSED") reject(new ChannelError(0, "not_listening", NOT_LISTENING));
      else if (err.code === "ECONNRESET" || err.code === "EPIPE") reject(new ChannelError(0, "connection_lost", CONNECTION_LOST));
      else reject(new ChannelError(0, "unreachable", unreachable(err.code || "no answer")));
    });
    if (data) req.write(data);
    req.end();
  });
}

export class Channel {
  token = "";
  /** How long one call may wait for the app's answer, in ms. A poll's own wait is 20 seconds. */
  timeout = 60_000;
  /** The first pause before a dropped poll is tried again, in ms; it doubles each time. */
  pollPause = 500;

  constructor(private readonly dir: string = dataDir()) {}

  /** Before each call that sends the hello or a token: whatever listens on the port must show it
   * knows the hello. Every call, not once: if the app stops and leaves editor-port behind, another
   * user on this PC could take the same port, and a remembered proof would still match. */
  private async prove(where: { port: number; hello: string }): Promise<void> {
    const nonce = crypto.randomBytes(24).toString("hex");
    const reply = await send(where.port, "GET", `/v1/hello?nonce=${nonce}&api=${API_VERSION}`, {}, undefined, this.timeout);
    const result = (reply.payload?.ok ? reply.payload.result : null) as { proof?: unknown; api?: unknown } | null;
    const proof = result ? String(result.proof ?? "") : "";
    const want = crypto.createHmac("sha256", where.hello).update(nonce).digest();
    const got = Buffer.from(proof, "hex");
    if (got.length !== want.length || !crypto.timingSafeEqual(got, want)) {
      if (reply.payload?.error?.code === "off") throw new ChannelError(reply.status, "off", OFF);
      if (reply.payload?.error?.code === "busy" && reply.status === 503) throw new ChannelError(503, "busy", BUSY);
      if (reply.payload?.error?.code === "extension_too_old") throw new ChannelError(reply.status, "extension_too_old", EXTENSION_TOO_OLD);
      throw new ChannelError(0, "not_kratosforge", NOT_KRATOSFORGE);
    }
    // Proven, so the version is KratosForge's own. An app from before versions were reported speaks 1.
    const api = result?.api === undefined ? 1 : result.api;
    if (typeof api !== "number" || !Number.isInteger(api) || api < 1) throw new ChannelError(0, "bad_reply", BAD_REPLY);
    if (api > API_VERSION) throw new ChannelError(0, "app_too_new", APP_TOO_NEW);
  }

  /** A call, tried again a few times if the app turned it away only because it had too many
   * connections open: that refusal comes before the app reads the request, so nothing ran. */
  private async request(method: string, url: string, body?: unknown, hello = false): Promise<unknown> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.once(method, url, body, hello);
      } catch (err) {
        if (!(err instanceof ChannelError) || err.code !== "busy" || attempt >= BUSY_RETRIES) throw err;
        await new Promise((r) => setTimeout(r, 250 * 2 ** attempt));
      }
    }
  }

  private async once(method: string, url: string, body?: unknown, hello = false): Promise<unknown> {
    const where = readPortFile(this.dir);
    if (!where) throw new ChannelError(0, "not_listening", NOT_LISTENING);
    await this.prove(where);
    const data = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    const headers: Record<string, string | number> = {};
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = data.length;
    }
    if (hello) headers["X-KratosForge-Hello"] = where.hello;
    else if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const { status, payload } = await send(where.port, method, url, headers, data, this.timeout);
    if (!payload) throw new ChannelError(status, "bad_reply", BAD_REPLY);
    if (payload.ok) return payload.result;
    if (payload.error?.code === "busy" && status !== 503) throw new ChannelError(status, "failed", unexpected(`busy, status ${status}`));
    throw refusal(status, payload.error?.code, payload.error?.message, url.startsWith("/v1/pair/"), hello);
  }

  /** Asks to pair; the app shows the request with ``code``, which this window shows too. */
  async ask(window: string, client: string): Promise<{ id: string; code: string; expires_in: number }> {
    return shaped(await this.request("POST", "/v1/pair", { window, client }, true),
      (v) => text(v.id, 200) && text(v.code, 64) && Number.isFinite(v.expires_in) && v.expires_in > 0 && v.expires_in <= 86_400);
  }

  async collect(id: string): Promise<{ state: string; token?: string; keep?: string }> {
    const got = shaped<{ state: string; token?: string | null; keep?: string | null }>(
      await this.request("GET", `/v1/pair/${encodeURIComponent(id)}`, undefined, true),
      (v) => text(v.state, 40) && (v.token === undefined || v.token === null || TOKEN.test(v.token))
        && (v.keep === undefined || v.keep === null || KEEPS.includes(v.keep)));
    if (got.token === null) delete got.token;
    if (got.keep === null) delete got.keep;
    return got as { state: string; token?: string; keep?: string };
  }

  /** Waits for the person to answer in the app. Resolves with the token and how long it's kept. */
  async pair(window: string, client: string, shown: (code: string) => void, cancelled: () => boolean = () => false,
    every = 1000): Promise<{ token: string; keep: string }> {
    const req = await this.ask(window, client);
    shown(req.code);
    const deadline = Date.now() + req.expires_in * 1000;
    while (Date.now() < deadline && !cancelled()) {
      const got = await this.collect(req.id);
      if (got.state === "paired" && got.token) {
        this.token = got.token;
        return { token: got.token, keep: got.keep || "session" };
      }
      if (got.state === "denied") throw new ChannelError(403, "denied", PAIR_DENIED);
      if (got.state === "expired") break;
      await new Promise((r) => setTimeout(r, every));
    }
    throw cancelled() ? new ChannelError(408, "cancelled", PAIR_CANCELLED) : new ChannelError(408, "expired", PAIR_UNANSWERED);
  }

  async status(): Promise<{ app: string; version: string; pairing: Record<string, unknown> }> {
    return shaped(await this.request("GET", "/v1/status"), () => true);
  }

  unpair(): Promise<unknown> {
    return this.request("DELETE", "/v1/pair");
  }

  async notebook(file: string): Promise<{ name: string }> {
    return shaped(await this.request("POST", "/v1/notebook", { path: file }), (v) => text(v.name, 4096));
  }

  async start(file: string, code: string): Promise<Snapshot> {
    const size = Buffer.byteLength(JSON.stringify({ path: file, code }));
    if (size > MAX_REQUEST) throw new ChannelError(0, "cell_too_large", cellTooLarge(size));
    return snapshot(await this.request("POST", "/v1/run", { path: file, code }));
  }

  async poll(file: string, version = -1, wait = 20): Promise<Snapshot> {
    return snapshot(await this.request("GET", `/v1/run?path=${encodeURIComponent(file)}&version=${version}&wait=${wait}`));
  }

  interrupt(file: string): Promise<unknown> {
    return this.request("POST", "/v1/interrupt", { path: file });
  }

  /** A poll of a running cell, asked again a few times if the connection drops or the app is slow to
   * answer: the cell is still running in the app, so one lost answer doesn't fail it here. */
  private async pollAgain(file: string, version: number): Promise<Snapshot> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await this.poll(file, version);
      } catch (err) {
        const code = err instanceof ChannelError ? err.code : "";
        const limit = code === "connection_lost" ? POLL_RETRIES : code === "timed_out" ? POLL_TIMEOUT_RETRIES : -1;
        if (attempt >= limit) throw err;
        await new Promise((r) => setTimeout(r, this.pollPause * 2 ** attempt));
      }
    }
  }

  /** Runs ``code`` in the notebook's session; ``progress`` gets each new snapshot while it runs.
   * ``cancelled`` says the person pressed Stop: before the cell is sent, it isn't sent at all; while
   * the start is on its way, the cell is interrupted as soon as the app says it started. Once
   * ``progress`` has had a snapshot, interrupting is the caller's job, so it's never asked twice. */
  async run(file: string, code: string, progress: (snap: Snapshot) => void, cancelled: () => boolean = () => false): Promise<Snapshot> {
    if (cancelled()) return { status: "cancelled", outputs: [] };
    let snap = await this.start(file, code);
    if (cancelled() && (snap.status === "running" || snap.status === "paused")) await this.interrupt(file).catch(() => undefined);
    progress(snap);
    let version = snap.version ?? -1;
    while (snap.status === "running" || snap.status === "paused") {
      const next = await this.pollAgain(file, version);
      // The session this cell ran in is gone (no session, or a new one counting from the start):
      // the app restarted. Its last output isn't the cell's result, so say so rather than end "ok".
      if (next.status === "idle" || (next.version ?? version) < version) throw new ChannelError(0, "restarted", RESTARTED);
      if ((next.version ?? -1) !== version || next.status !== snap.status) progress(next);
      version = next.version ?? version;
      snap = next;
    }
    return snap;
  }
}
