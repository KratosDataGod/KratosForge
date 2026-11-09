// KratosForge's cell outputs (sidecar/fabric_desktop/notebook.py) as VS Code notebook outputs. No
// VS Code API here: each output becomes a list of {mime, data} items, or an error, that
// extension.ts hands to NotebookCellOutputItem.

export type Item = { mime: string; text?: string; bytes?: Uint8Array };
export type Output = { items: Item[] } | { error: { name: string; message: string; stack?: string } };

const STDOUT = "application/vnd.code.notebook.stdout";

// The most one output item may hold, as the app's own cap on a cell's tables, charts and HTML
// (RICH_CHARS in notebook.py). An item past it shows a line saying so instead.
export const MAX_ITEM = 16_000_000;

// Control characters other than tab, newline, carriage return, backspace and escape (which the
// output renderer reads as colors), and the C1 controls: shown as nothing rather than passed on.
const CONTROL = /[\u0000-\u0007\u000b\u000c\u000e-\u001a\u001c-\u001f\u007f-\u009f]/g;

function plain(text: string): string {
  return text.replace(CONTROL, "");
}

function tooBig(what: string, size: number): Item {
  return { mime: "text/plain", text: `This ${what} is ${(size / 1_000_000).toFixed(1)} MB, more than VS Code is sent `
    + `(${MAX_ITEM / 1_000_000} MB). Open the notebook in KratosForge to see it.` };
}

/** JSON for a renderer, or undefined when the value can't be written (nested too deep, or a cycle). */
function json(value: unknown): string | undefined {
  try {
    return JSON.stringify(value) ?? "null";
  } catch {
    return undefined;
  }
}

const UNREADABLE: Item = { mime: "text/plain", text: "This output is nested too deeply to show here. Open the notebook in KratosForge to see it." };

/** Items whose text or bytes pass MAX_ITEM become one line saying so. */
function capped(items: Item[], what: string): Item[] {
  const size = Math.max(...items.map((i) => i.bytes?.length ?? i.text?.length ?? 0));
  return size > MAX_ITEM ? [tooBig(what, size)] : items;
}

function escape(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? json(value) ?? "…" : String(value);
}

function table(out: Record<string, unknown>): Item[] {
  const columns = (out.columns as string[]) || [];
  const rows = (out.rows as unknown[][]) || [];
  const total = out.total_rows;
  const note = `${rows.length} of ${typeof total === "number" ? total.toLocaleString("en-US") : total ?? rows.length} rows`;
  const html = `<table><thead><tr>${columns.map((c) => `<th>${escape(c)}</th>`).join("")}</tr></thead><tbody>`
    + rows.map((r) => `<tr>${r.map((v) => `<td>${escape(cell(v))}</td>`).join("")}</tr>`).join("")
    + `</tbody></table><p>${escape(note)}</p>`;
  const plain = [columns.join("\t"), ...rows.map((r) => r.map(cell).join("\t")), note].join("\n");
  return [{ mime: "text/html", text: html }, { mime: "text/plain", text: plain }];
}

export function convert(out: Record<string, unknown>): Output {
  try {
    return convertOne(out);
  } catch {
    return { items: [UNREADABLE] };
  }
}

function chart(mime: string, value: unknown, name: string): Output {
  const text = json(value);
  if (text === undefined) return { items: [UNREADABLE] };
  return { items: capped([{ mime, text }, { mime: "text/plain",
    text: `A ${name} chart. Install the Jupyter extension's renderers to see it here, or open the notebook in KratosForge.` }], `${name} chart`) };
}

function convertOne(out: Record<string, unknown>): Output {
  const text = (key: string) => String(out[key] ?? "");
  switch (out.type) {
    case "stream":
      return { items: capped([{ mime: STDOUT, text: plain(text("text")) }], "printed text") };
    case "text":
      return { items: capped([{ mime: "text/plain", text: plain(text("text")) }], "text") };
    case "markdown":
      return { items: capped([{ mime: "text/markdown", text: text("text") }], "Markdown") };
    case "html":
      return { items: capped([{ mime: "text/html", text: text("html") }], "HTML") };
    case "image": {
      const mime = text("mime") || "image/png";
      const data = text("data");
      if (data.length * 0.75 > MAX_ITEM) return { items: [tooBig("image", Math.floor(data.length * 0.75))] };
      const bytes = Buffer.from(data, "base64");
      return { items: [mime === "image/svg+xml" ? { mime, text: bytes.toString("utf8") } : { mime, bytes: new Uint8Array(bytes) }] };
    }
    case "json": {
      const data = json(out.data);
      return { items: data === undefined ? [UNREADABLE] : capped([{ mime: "application/json", text: data }], "JSON") };
    }
    case "table":
      return { items: capped(table(out), "table") };
    case "vegalite":
      return chart("application/vnd.vegalite.v5+json", out.spec, "Vega-Lite");
    case "plotly":
      return chart("application/vnd.plotly.v1+json", out.figure, "Plotly");
    case "error": {
      const stack = plain(text("traceback")).slice(0, MAX_ITEM);
      return { error: { name: plain(text("ename")).slice(0, 200) || "Error", message: plain(text("evalue")).slice(0, MAX_ITEM),
        stack: stack || undefined } };
    }
    case "widget":
      return { items: [{ mime: "text/plain", text: "A widget. It works in KratosForge; open the notebook there to use it." }] };
    default: {
      const data = json(out);
      return { items: data === undefined ? [UNREADABLE] : capped([{ mime: "text/plain", text: plain(data) }], "output") };
    }
  }
}

/** Whether the cell ended with an error: its last snapshot's status, or an error output. */
export function failed(status: string, outputs: Array<Record<string, unknown>> = []): boolean {
  return status === "error" || outputs.some((o) => o.type === "error");
}
