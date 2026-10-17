// The code editor for notebook cells and the SQL query page: CodeMirror 6 with Python, SQL, HTML and Markdown.
// Built into codemirror.js (window.CM) by `npm run build` in this folder; app.js uses only window.CM.
export { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection,
  dropCursor, highlightSpecialChars, placeholder, rectangularSelection, crosshairCursor, ViewPlugin,
  Decoration } from "@codemirror/view";
export { EditorState, Compartment, Prec, RangeSetBuilder } from "@codemirror/state";
export { defaultKeymap, history, historyKeymap, indentWithTab, toggleComment, undo, redo,
  isolateHistory } from "@codemirror/commands";
export { searchKeymap, highlightSelectionMatches, openSearchPanel } from "@codemirror/search";
export { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap, startCompletion,
  completeFromList } from "@codemirror/autocomplete";
export { bracketMatching, indentOnInput, foldGutter, foldKeymap, syntaxHighlighting, HighlightStyle,
  syntaxTree } from "@codemirror/language";
export { tags } from "@lezer/highlight";
export { python } from "@codemirror/lang-python";
export { sql, StandardSQL, SQLDialect, MSSQL, PostgreSQL } from "@codemirror/lang-sql";
export { html } from "@codemirror/lang-html";
export { markdown } from "@codemirror/lang-markdown";
