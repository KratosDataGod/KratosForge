// Markdown for notebook cells, Markdown outputs and Learn pages: markdown-it (CommonMark and GitHub
// tables, strikethrough, bare links) with $...$ and $$...$$ math from markdown-it-texmath, drawn by
// KaTeX as MathML only (no CSS or fonts to load). Built into markdown.js (window.MD) by
// `npm ci --ignore-scripts && npm run build` in this folder, so every package comes from
// package-lock.json at its recorded version and hash.
//
// Nothing here makes HTML from text: raw HTML in Markdown stays text (html: false), app.js builds the
// page from parse()'s tokens with h(), and math() lets KaTeX build MathML elements in the DOM.
import MarkdownIt from "markdown-it";
import texmath from "markdown-it-texmath";
import katex from "katex";

const md = new MarkdownIt({ html: false, linkify: true, typographer: false });
md.use(texmath, { engine: katex, delimiters: "dollars" });

export function parse(src) {
  return md.parse(String(src), {});
}

// markdown-it's own check: refuses javascript:, vbscript:, file: and data: (except images of a few types).
export function validLink(url) {
  return md.validateLink(String(url));
}

// Math as MathML elements appended to `el`. trust: false refuses \href, \url, \includegraphics and
// \htmlClass; maxSize and maxExpand keep a hostile formula from growing without end.
export function math(tex, el, display) {
  katex.render(String(tex), el, {
    displayMode: !!display, output: "mathml", throwOnError: false, trust: false, strict: "ignore",
    maxSize: 50, maxExpand: 1000,
  });
}
