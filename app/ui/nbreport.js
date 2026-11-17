// Notebook > Share > Open as report (Preview, parity item 18): the notebook's Markdown and the outputs of
// its last run, without the code, in a window of its own. The page is the engine's web page export in
// report form (notebook_formats.to_html), so what you see is what Export as HTML writes and what a
// recipient gets. Nothing in it runs: every text is escaped by the engine, and the page is parsed inert
// and shown with only its plain elements (nbfPrintNodes). The main window sends the report to the
// engine (notebook_report_open) and opens the window (a pop-out, "report"); the window reads the report
// back (notebook_report_page) when it opens and each time it comes forward.

const reportPage = { data: null, error: null, at: null };

// The run the outputs are from: the last cell to finish on this PC, or the outputs saved with the notebook.
function nbReportRanLine(cells) {
  const last = Math.max(0, ...cells.map((c) => c.finishedAtMs || 0));
  return last ? `Outputs from the run at ${dateTimeText(last)}, on this PC.` : "Outputs as saved with the notebook.";
}

// Whether the notebook changed after its run: a cell edited since it ran, or the dependency check's
// stale cells (deps.js).
function nbReportStale(nb) {
  if (nb.cells.some((c) => c.type === "code" && c.ran && c.ran.source !== c.source)) return true;
  try { return Object.keys(depsStaleNow(nb)).length > 0; } catch { return false; }
}

async function openNotebookReport() {
  const nb = state.notebook;
  if (!nb) return;
  const cells = nbfExportPayload(nb, "html").cells;
  const made = await attempt(() => rpc("notebook_report_open", { name: nb.name, cells, ran: nbReportRanLine(nb.cells),
    stale: nbReportStale(nb) }), "Could not make the report");
  if (!made) return;
  await attempt(() => invoke("pop_out_open", { section: "report", onTop: popOutOnTop("report") }), "Could not open the report's window");
}

// The report window's start (popout.js): its own page, read from the engine now and when it comes forward.
async function startReportWindow() {
  registerFeature({ id: "notebookReport", views: { notebookReport: renderNotebookReport } });
  state.view = "notebookReport";
  nav.stack = [navEntry("notebookReport")];
  nav.index = 0;
  await loadSettings().catch(() => {});
  await reportLoad();
  addEventListener("focus", () => reportLoad());
}

async function reportLoad() {
  try {
    const data = await rpc("notebook_report_page");
    if (reportPage.data && data?.at === reportPage.at) return;  // the same report: keep your place in it
    Object.assign(reportPage, { data, error: null, at: data?.at ?? null });
  } catch (err) {
    Object.assign(reportPage, { data: null, error: err });
  }
  render();
}

function renderNotebookReport() {
  const r = reportPage.data;
  if (!r?.html) {
    setView(h("div", { class: "page nb-report" }, reportPage.error
      ? h("p", { class: "fail-text", role: "alert" }, `Couldn't read the report: ${friendlyError(reportPage.error).text} Open it again from the notebook (Share > Open as report).`)
      : h("p", { class: "muted-text" }, "There's no report to show. Open one from a notebook: Share > Open as report.")));
    return;
  }
  document.title = `${r.name} · report`;
  const body = h("article", { class: "nb-report-page", "aria-label": `${r.name}, report` });
  body.append(...nbfPrintNodes(r.html));
  const exportHtml = async () => {
    const out = await attempt(() => rpc("notebook_report_export"), "Could not export the report");
    if (out) toast(`Saved to ${out.path}`);
  };
  setView(h("div", { class: "page nb-report" },
    h("div", { class: "nb-report-bar row wrap" },
      h("span", { class: "muted-text" }, "Report, read only"), previewTag(), learnMore("notebook-as-a-read-only-report-preview"), h("span", { class: "spacer" }),
      btn("Export as HTML", "arrowDownload", exportHtml, "tool outline", { title: "Save this page as a new .html file in Downloads" }),
      btn("Export as PDF", "arrowDownload", () => nbfPrintHtml(r.html), "tool outline", { title: "Print this page; pick Save as PDF as the printer" }),
      btn("Open the notebook", "notebook", () => attempt(() => invoke("pop_out_close", { section: "report" }), "Could not close the report"), "tool",
        { title: "Close the report and go back to the notebook in the main window" })),
    r.stale ? h("div", { class: "callout warn nb-report-stale", role: "status" },
      h("span", {}, h("b", {}, "The notebook changed after this run."),
        " The report shows the outputs from that run; run the notebook and open the report again to bring it up to date.")) : null,
    body));
}
