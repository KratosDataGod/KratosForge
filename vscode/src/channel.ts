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
  const [port, hello] = text.trim().split(/\s+/);
  const n = Number(port);
  return Number.isInteger(n) && n > 0 && n < 65536 && hello ? { port: n, hello } : null;
}

export const NOT_LISTENING = "KratosForge isn't running, or Let VS Code run notebooks is off in its Settings > Integrations.";
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
    const req = http.request({ host: "127.0.0.1", port, method, path: url, headers: { Host: `127.0.0.1:${port}`, ...headers },
      timeout }, (res) => {
      if (Number(res.headers["content-length"]) > MAX_REPLY) {
        req.destroy();
        reject(new ChannelError(0, "too_large", TOO_LARGE));
        return;
      }
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
    let timedOut = false;
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
      if (reply.payload?.error?.code === "off") throw new ChannelError(reply.status, "off", reply.payload.error.message || NOT_LISTENING);
      if (reply.payload?.error?.code === "busy" && reply.status === 503) throw new ChannelError(503, "busy", BUSY);
      if (reply.payload?.error?.code === "extension_too_old") throw new ChannelError(reply.status, "extension_too_old", EXTENSION_TOO_OLD);
      throw new ChannelError(0, "not_kratosforge", NOT_KRATOSFORGE);
    }
    // Proven, so the version is KratosForge's own. An app from before versions were reported speaks 1.
    const api = result?.api === undefined ? 1 : result.api;
    if (typeof api !== "number" || !Number.isInteger(api) || api < 1) throw new ChannelError(0, "bad_reply", "KratosForge sent a reply this extension can't read.");
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
    if (!payload) throw new ChannelError(status, "bad_reply", "KratosForge sent a reply this extension can't read.");
    if (payload.ok) return payload.result;
    if (status === 503 && payload.error?.code === "busy") throw new ChannelError(503, "busy", BUSY);
    throw new ChannelError(status, payload.error?.code || "failed", payload.error?.message || "KratosForge refused it.");
  }

  /** Asks to pair; the app shows the request with ``code``, which this window shows too. */
  ask(window: string, client: string): Promise<{ id: string; code: string; expires_in: number }> {
    return this.request("POST", "/v1/pair", { window, client }, true) as Promise<{ id: string; code: string; expires_in: number }>;
  }

  collect(id: string): Promise<{ state: string; token?: string; keep?: string }> {
    return this.request("GET", `/v1/pair/${encodeURIComponent(id)}`, undefined, true) as Promise<{ state: string; token?: string; keep?: string }>;
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
      if (got.state === "denied") throw new ChannelError(403, "denied", "Pairing was denied in KratosForge.");
      if (got.state === "expired") break;
      await new Promise((r) => setTimeout(r, every));
    }
    throw new ChannelError(408, "expired", cancelled() ? "Pairing was cancelled." : "Nobody answered in KratosForge, so this window isn't paired.");
  }

  status(): Promise<{ app: string; version: string; pairing: Record<string, unknown> }> {
    return this.request("GET", "/v1/status") as Promise<{ app: string; version: string; pairing: Record<string, unknown> }>;
  }

  unpair(): Promise<unknown> {
    return this.request("DELETE", "/v1/pair");
  }

  notebook(file: string): Promise<{ name: string }> {
    return this.request("POST", "/v1/notebook", { path: file }) as Promise<{ name: string }>;
  }

  start(file: string, code: string): Promise<Snapshot> {
    const size = Buffer.byteLength(JSON.stringify({ path: file, code }));
    if (size > MAX_REQUEST) return Promise.reject(new ChannelError(0, "cell_too_large", cellTooLarge(size)));
    return this.request("POST", "/v1/run", { path: file, code }) as Promise<Snapshot>;
  }

  poll(file: string, version = -1, wait = 20): Promise<Snapshot> {
    return this.request("GET", `/v1/run?path=${encodeURIComponent(file)}&version=${version}&wait=${wait}`) as Promise<Snapshot>;
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
        const passing = err instanceof ChannelError && (err.code === "connection_lost" || err.code === "timed_out");
        if (!passing || attempt >= POLL_RETRIES) throw err;
        await new Promise((r) => setTimeout(r, this.pollPause * 2 ** attempt));
      }
    }
  }

  /** Runs ``code`` in the notebook's session; ``progress`` gets each new snapshot while it runs. */
  async run(file: string, code: string, progress: (snap: Snapshot) => void): Promise<Snapshot> {
    let snap = await this.start(file, code);
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
