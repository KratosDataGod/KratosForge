// KratosForge's cell outputs (sidecar/fabric_desktop/notebook.py) as VS Code notebook outputs. No
// VS Code API here: each output becomes a list of {mime, data} items, or an error, that
// extension.ts hands to NotebookCellOutputItem.

export type Item = { mime: string; text?: string; bytes?: Uint8Array };
export type Output = { items: Item[] } | { error: { name: string; message: string; stack?: string } };

const STDOUT = "application/vnd.code.notebook.stdout";

function escape(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string);
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
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
  const text = (key: string) => String(out[key] ?? "");
  switch (out.type) {
    case "stream":
      return { items: [{ mime: STDOUT, text: text("text") }] };
    case "text":
      return { items: [{ mime: "text/plain", text: text("text") }] };
    case "markdown":
      return { items: [{ mime: "text/markdown", text: text("text") }] };
    case "html":
      return { items: [{ mime: "text/html", text: text("html") }] };
    case "image": {
      const mime = text("mime") || "image/png";
      const bytes = Buffer.from(text("data"), "base64");
      return { items: [mime === "image/svg+xml" ? { mime, text: bytes.toString("utf8") } : { mime, bytes: new Uint8Array(bytes) }] };
    }
    case "json":
      return { items: [{ mime: "application/json", text: JSON.stringify(out.data) }] };
    case "table":
      return { items: table(out) };
    case "vegalite":
      return { items: [{ mime: "application/vnd.vegalite.v5+json", text: JSON.stringify(out.spec) },
        { mime: "text/plain", text: "A Vega-Lite chart. Install the Jupyter extension's renderers to see it here, or open the notebook in KratosForge." }] };
    case "plotly":
      return { items: [{ mime: "application/vnd.plotly.v1+json", text: JSON.stringify(out.figure) },
        { mime: "text/plain", text: "A Plotly chart. Install the Jupyter extension's renderers to see it here, or open the notebook in KratosForge." }] };
    case "error":
      return { error: { name: text("ename") || "Error", message: text("evalue"), stack: text("traceback") || undefined } };
    case "widget":
      return { items: [{ mime: "text/plain", text: "A widget. It works in KratosForge; open the notebook there to use it." }] };
    default:
      return { items: [{ mime: "text/plain", text: JSON.stringify(out) }] };
  }
}

/** Whether the cell ended with an error: its last snapshot's status, or an error output. */
export function failed(status: string, outputs: Array<Record<string, unknown>> = []): boolean {
  return status === "error" || outputs.some((o) => o.type === "error");
}
