# Organise a notebook's cells

Long notebooks are easier to work in when you can fold them by section, name the cells that
matter and move cells around without cutting and pasting. Everything here changes only the
notebook file on this PC; nothing runs and nothing is written to OneLake.

## Collapse a section

A Markdown cell that starts with a heading (`#` to `######`) gets a chevron in its gutter. Click it
to hide the cells under the heading, up to the next heading of the same or a higher level. A
collapsed heading shows **N cells hidden. Click to show.** underneath.

- **View > Collapse all sections** folds every heading; when any section is folded the same button
  reads **Expand all sections**.
- Sections nest: open a `#` section and its collapsed `##` sections stay folded.
- Hidden cells still run with **Run all**, and **J**/**K** and the arrow keys skip them.
- Clicking a cell in **View > Outline** opens the sections that hide it.
- Moving a collapsed heading (**Move up**/**Move down**, **Alt+Up**/**Alt+Down** or dragging it) takes its hidden cells with it, as in JupyterLab. Cells you selected next to each other on screen move together even when a collapsed section sits between them.
- A move, a new cell or a paste never hides a cell: if it lands inside a collapsed section, the section opens.

The collapsed state is saved in the cell's `jp-MarkdownHeadingCollapsed` metadata, the key
JupyterLab uses, so a notebook opens folded the same way in JupyterLab.

## Titles

Open a cell's **...** menu and choose **Add title**. The title shows in the cell's header (click it
to rename) and, for code cells, in the outline under its section. **Remove title** is in the same
menu. Titles are saved where Databricks keeps them (`application/vnd.databricks.v1+cell` > `title`
in the cell metadata), so they survive a round trip through Databricks; Fabric ignores them.

## Tags

**... > Add tag** adds a Jupyter cell tag (letters, numbers and `- _ . :`, up to 40 characters).
Tags show as chips in the header; the **×** on a chip removes it. Adding `parameters` makes the
cell the notebook's parameter cell, the same as **Edit > Parameter cell**, so a notebook keeps one.
Tags are saved in the cell's `metadata.tags`, as Fabric and Jupyter keep them.

## Duplicate

**... > Duplicate cell** puts a copy below with the same source, language, title and tags. The copy
has no output, isn't the parameter cell and isn't collapsed.

## Drag to move

Hover a cell and drag the **⋮⋮** grip at its top left onto another cell. A line shows whether it
lands above or below. **Move up** and **Move down** in the header still work.
