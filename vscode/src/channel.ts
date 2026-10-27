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

interface Reply { status: number; payload: { ok?: boolean; result?: unknown; error?: { code?: string; message?: string } } | null }

function send(port: number, method: string, url: string, headers: Record<string, string | number>, data?: Buffer): Promise<Reply> {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method, path: url, headers: { Host: `127.0.0.1:${port}`, ...headers },
      timeout: 60_000 }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c: Buffer) => chunks.push(c));
      res.on("end", () => {
        try {
          resolve({ status: res.statusCode || 0, payload: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
        } catch {
          resolve({ status: res.statusCode || 0, payload: null });
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error("KratosForge didn't answer in time.")));
    req.on("error", (err: NodeJS.ErrnoException) =>
      reject(err.code === "ECONNREFUSED" ? new ChannelError(0, "not_listening", NOT_LISTENING) : err));
    if (data) req.write(data);
    req.end();
  });
}

export class Channel {
  token = "";

  constructor(private readonly dir: string = dataDir()) {}

  /** Before each call that sends the hello or a token: whatever listens on the port must show it
   * knows the hello. Every call, not once: if the app stops and leaves editor-port behind, another
   * user on this PC could take the same port, and a remembered proof would still match. */
  private async prove(where: { port: number; hello: string }): Promise<void> {
    const nonce = crypto.randomBytes(24).toString("hex");
    const reply = await send(where.port, "GET", `/v1/hello?nonce=${nonce}`, {});
    const proof = reply.payload?.ok ? String((reply.payload.result as { proof?: unknown })?.proof ?? "") : "";
    const want = crypto.createHmac("sha256", where.hello).update(nonce).digest();
    const got = Buffer.from(proof, "hex");
    if (got.length !== want.length || !crypto.timingSafeEqual(got, want)) {
      if (reply.payload?.error?.code === "off") throw new ChannelError(reply.status, "off", reply.payload.error.message || NOT_LISTENING);
      throw new ChannelError(0, "not_kratosforge", NOT_KRATOSFORGE);
    }
  }

  private async request(method: string, url: string, body?: unknown, hello = false): Promise<unknown> {
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
    const { status, payload } = await send(where.port, method, url, headers, data);
    if (!payload) throw new ChannelError(status, "bad_reply", "KratosForge sent a reply this extension can't read.");
    if (payload.ok) return payload.result;
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
    return this.request("POST", "/v1/run", { path: file, code }) as Promise<Snapshot>;
  }

  poll(file: string, version = -1, wait = 20): Promise<Snapshot> {
    return this.request("GET", `/v1/run?path=${encodeURIComponent(file)}&version=${version}&wait=${wait}`) as Promise<Snapshot>;
  }

  interrupt(file: string): Promise<unknown> {
    return this.request("POST", "/v1/interrupt", { path: file });
  }

  /** Runs ``code`` in the notebook's session; ``progress`` gets each new snapshot while it runs. */
  async run(file: string, code: string, progress: (snap: Snapshot) => void): Promise<Snapshot> {
    let snap = await this.start(file, code);
    progress(snap);
    let version = snap.version ?? -1;
    while (snap.status === "running" || snap.status === "paused") {
      const next = await this.poll(file, version);
      if ((next.version ?? -1) !== version || next.status !== snap.status) progress(next);
      version = next.version ?? version;
      snap = next;
    }
    return snap;
  }
}
