// Tables and files from the notebook's Explorer, as in Fabric: drag one into a cell to put its
// name or the code that loads it where you drop it, or right-click it for the ways to load it
// (Spark, Polars, pandas, SQL) and to copy its name. Clicking still adds a cell that loads it.
// Only code is written into the notebook; nothing is read until you run the cell.

const DATA_ITEM_MIME = "application/x-kratosforge-data-item";

const dataItemQuote = (s) => JSON.stringify(s);

function dataItemName(item) {
  return item.kind === "table" ? `${item.schema}.${item.table}` : item.path;
}

// The ways to load an item: [label, code]. The first is what a click inserts (the code it always did).
function dataItemLoads(item) {
  const name = dataItemName(item);
  if (item.kind === "table") {
    return [
      ["Load with Polars", `df = lakehouse.read_table(${dataItemQuote(name)})\ndf`],
      ["Load with Spark", `df = spark.read.table(${dataItemQuote(name)})\ndisplay(df)`],
      ["Load with pandas", `df = lakehouse.read_table(${dataItemQuote(name)}).to_pandas()\ndf`],
      ["Query with SQL", `%%sql\nSELECT * FROM ${name} LIMIT 1000`],
    ];
  }
  const ext = name.split(".").pop().toLowerCase();
  const polars = { csv: "read_csv", parquet: "read_parquet", json: "read_json" }[ext];
  const spark = { csv: `spark.read.option("header", True).csv(${dataItemQuote(name)})`, parquet: `spark.read.parquet(${dataItemQuote(name)})`,
    json: `spark.read.json(${dataItemQuote(name)})` }[ext];
  const loads = [["Load with Polars", readSnippet(name)]];
  if (spark) loads.push(["Load with Spark", `df = ${spark}\ndisplay(df)`]);
  if (polars) loads.push(["Load with pandas", `df = lakehouse.${polars}(${dataItemQuote(name)}).to_pandas()\ndf`]);
  if (ext !== "parquet") loads.push(["Read the file's text", `with open(lakehouse.local_path(${dataItemQuote(name)}), encoding="utf-8") as f:\n    text = f.read()\nprint(text[:2000])`]);
  return loads;
}

// What a drop puts in a cell: the full load code on an empty line of a Python cell, otherwise just
// the name, quoted in Python unless it lands inside a string, as code in Markdown.
function dataItemDropText(item, lang, { blankLine = false, inString = false } = {}) {
  const name = dataItemName(item);
  if (lang === "sql") return name;
  if (lang === "markdown") return `\`${name}\``;
  if (lang === "html") return name;
  if (inString) return name;
  if (blankLine) return dataItemLoads(item)[0][1];
  return dataItemQuote(name);
}

// Attributes renderTree (app.js) adds to a table or file row in the notebook's Explorer.
function dataItemAttrs(item) {
  return {
    draggable: "true",
    ondragstart: (e) => {
      e.dataTransfer.setData(DATA_ITEM_MIME, JSON.stringify(item));
      e.dataTransfer.setData("text/plain", dataItemName(item));
      e.dataTransfer.effectAllowed = "copy";
      document.querySelector(".cells")?.classList.add("drop-ready");
    },
    ondragend: () => document.querySelector(".cells")?.classList.remove("drop-ready"),
    oncontextmenu: (e) => {
      e.preventDefault();
      // Opened where the mouse is, as a context menu; Esc returns focus to the row.
      const row = e.currentTarget;
      const at = { getBoundingClientRect: () => ({ left: e.clientX, bottom: e.clientY - 4 }), setAttribute: () => {},
        focus: () => row.focus(), get isConnected() { return row.isConnected; } };
      showMenu(at, dataItemMenu(item));
    },
  };
}

function dataItemMenu(item) {
  const name = dataItemName(item);
  return [
    ...dataItemLoads(item).map(([label, code]) => ({ label, icon: label.startsWith("Query") ? "database" : "add", action: () => insertCell(code) })),
    { label: item.kind === "table" ? "Copy name" : "Copy path", icon: "copyArrowRight", action: async () => {
      try {
        await navigator.clipboard.writeText(name);
        toast(`Copied ${name}`);
      } catch {
        toast(`Couldn't copy. The ${item.kind === "table" ? "name" : "path"} is ${name}`);
      }
    } },
  ];
}

function dataItemFrom(e) {
  try {
    const raw = e.dataTransfer?.getData(DATA_ITEM_MIME);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

const isDataItemDrag = (e) => [...(e.dataTransfer?.types || [])].includes(DATA_ITEM_MIME);

// The editor side: a drop inserts at the drop point, and the cell's undo takes it back.
function dataItemDrop(cell) {
  return CM.ViewPlugin.fromClass(class {}, {
    eventHandlers: {
      dragover(e) {
        if (!isDataItemDrag(e)) return false;
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";  // a frozen cell takes the drop to say why it refuses it
        return false;  // CodeMirror still draws its drop cursor
      },
      drop(e, view) {
        const item = dataItemFrom(e);
        if (!item) return false;
        e.preventDefault();
        e.stopPropagation();
        document.querySelector(".cells")?.classList.remove("drop-ready");
        if (cell.meta?.frozen) { toast("This cell is frozen. Unfreeze it to drop code into it."); return true; }
        const pos = view.posAtCoords({ x: e.clientX, y: e.clientY }) ?? view.state.doc.length;
        const line = view.state.doc.lineAt(pos);
        const inString = ["String", "FormatString"].includes(CM.syntaxTree(view.state).resolveInner(pos, -1).name) && cellLang(cell) === "python";
        const blankLine = !line.text.trim();
        let text = dataItemDropText(item, cellLang(cell), { blankLine, inString });
        let from = pos;
        if (blankLine && text.includes("\n")) {
          from = line.from;
          const indent = line.text;
          text = text.split("\n").map((l) => indent + l).join("\n");
          view.dispatch({ changes: { from: line.from, to: line.to, insert: text }, selection: { anchor: line.from + text.length } });
        } else {
          view.dispatch({ changes: { from, insert: text }, selection: { anchor: from + text.length } });
        }
        view.focus();
        return true;
      },
    },
  });
}

// Dropped between cells or below them: a new cell that loads the item.
document.addEventListener("dragover", (e) => {
  if (state.view === "notebook" && isDataItemDrag(e) && e.target.closest?.(".cells") && !e.target.closest(".cm-editor")) e.preventDefault();
});
document.addEventListener("drop", (e) => {
  if (state.view !== "notebook" || !e.target.closest?.(".cells") || e.target.closest(".cm-editor")) return;
  const item = dataItemFrom(e);
  if (!item) return;
  e.preventDefault();
  document.querySelector(".cells")?.classList.remove("drop-ready");
  const over = e.target.closest("[data-cell]");
  const cell = over && state.notebook.cells.find((c) => String(c.id) === over.dataset.cell);
  if (cell) selectCell(cell);
  insertCell(dataItemLoads(item)[0][1]);
});
