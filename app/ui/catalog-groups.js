// OneLake catalog grouping: by environment (Dev / Test / Prod) and by solution, the work a
// workspace belongs to. Environment comes from the Metadata environments first (the workspace
// given to Dev, Test and Prod, and each one's Keys workspace), else from Dev, Test or Prod in the
// workspace name. The solution is the workspace name without that word, so "Adv Wrks DE 2 Dev",
// "... Test" and "... Prod" sit together.

const ENVS = ["dev", "test", "prod"];
const ENV_NAMES = { dev: "Dev", test: "Test", prod: "Prod", none: "No environment" };

// A separate word in the name: "Sales Dev", "Sales-DEV", "Sales [Prod]", "DEV_Sales", "Sales (UAT)".
const ENV_WORD = /(^|[\s_\-.[(])(dev|development|test|tst|uat|qa|prod|production|prd)(?=$|[\s_\-.\])])/gi;
const ENV_OF_WORD = { dev: "dev", development: "dev", test: "test", tst: "test", uat: "test", qa: "test",
  prod: "prod", production: "prod", prd: "prod" };

// {workspace_id: env} from the metadata project's environments. A workspace two environments share
// (the Keys workspace, #293) goes to the first of them, or with strictest to the strictest (Prod).
function envWorkspaces(environments, strictest = false) {
  const out = {};
  for (const env of strictest ? [...ENVS].reverse() : ENVS) {
    const cfg = environments?.[env] || {};
    for (const id of [cfg.workspace_id, cfg.keys_workspace_id]) if (id && !out[id]) out[id] = env;
  }
  return out;
}

// The last such word: the environment is usually a suffix ("Production Planning Dev" is Dev).
function envWord(name) {
  const all = [...String(name || "").matchAll(ENV_WORD)];
  return all.length ? all[all.length - 1] : null;
}

function envFromName(name) {
  const m = envWord(name);
  return m ? ENV_OF_WORD[m[2].toLowerCase()] : null;
}

function workspaceEnv(id, name, mapped = {}) {
  return mapped[id] || envFromName(name) || "none";
}

function solutionOf(name) {
  const text = String(name || "");
  const m = envWord(text);
  if (!m) return text.trim();
  const start = m.index + m[1].length;
  let rest = text.slice(0, start) + text.slice(start + m[2].length);
  rest = rest.replace(/\[\s*\]|\(\s*\)/g, " ")          // "Sales [Prod]" -> "Sales [ ]" -> "Sales"
    .replace(/[\s_\-.]{2,}/g, (s) => (s.includes(" ") ? " " : s[0]))
    .replace(/^[\s_\-.]+|[\s_\-.]+$/g, "");
  return rest || text.trim();
}

// Rows grouped for the catalog. mode: "env" | "solution" | "both". Each group:
// {key, label, env?, rows, children?}. Groups keep the rows' order (the table's sort).
function groupCatalog(rows, mode, envOf) {
  const solution = (r) => solutionOf(r.workspace_name) || r.workspace_name;
  const envOrder = (e) => (e === "none" ? 9 : ENVS.indexOf(e));
  const bucket = (list, keyOf) => {
    const map = new Map();
    for (const r of list) {
      const k = keyOf(r);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    }
    return map;
  };
  if (mode === "env") {
    return [...bucket(rows, envOf)].sort(([a], [b]) => envOrder(a) - envOrder(b))
      .map(([env, list]) => ({ key: `env:${env}`, label: ENV_NAMES[env], env, rows: list }));
  }
  const solutions = [...bucket(rows, (r) => solution(r).toLowerCase())]
    .map(([k, list]) => ({ key: `sol:${k}`, label: solution(list[0]), rows: list }))
    .sort((a, b) => a.label.localeCompare(b.label, undefined, { sensitivity: "base" }));
  if (mode !== "both") return solutions;
  for (const g of solutions) {
    g.children = [...bucket(g.rows, envOf)].sort(([a], [b]) => envOrder(a) - envOrder(b))
      .map(([env, list]) => ({ key: `${g.key}|${env}`, label: ENV_NAMES[env], env, rows: list,
        workspaces: [...new Set(list.map((r) => r.workspace_name))] }));
  }
  return solutions;
}

if (typeof module !== "undefined") module.exports = { envWorkspaces, envFromName, workspaceEnv, solutionOf, groupCatalog };
