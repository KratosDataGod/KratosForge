# Writing a Learn lesson (Preview)

KratosForge Learn is built from lessons and labs that ship inside the app. They run on this PC,
work offline, and never publish anything to Fabric. This page is for people who write or review
them. People use them on the [Learn](../learn.md) page; the plan is in issue #1065 and the stories it links to.

## Where content lives

Each module or lab is a folder under `sidecar/fabric_desktop/learn/content/`, named by its id:
`LM-01` for a module, `LAB-01` for a lab. It holds:

| File | What it is |
| --- | --- |
| `module.json` | What the module is, what it teaches and how its exercises are checked |
| `lesson.md` | The lesson, in Markdown. Short sections, an example in each, and a "Try it" at the end |
| `setup.sql`, `starter.sql`, `solution.sql` (or `.py`) | Data for the exercise, what the learner starts from, and the reference answer |

`content/paths.json` lists the learning paths and the modules in each, in order.

## module.json

| Field | What to put |
| --- | --- |
| `schema` | `kratosforge/learn-module@1` |
| `id`, `kind` | The folder's name; `module` or `lab` |
| `title`, `path`, `level`, `minutes` | Title; a path id from `paths.json`; `beginner`, `intermediate` or `advanced`; 1 to 120 |
| `objectives` | What the learner can do afterwards, one line each |
| `prerequisites` | Module ids to do first |
| `platforms`, `engines` | Where the lesson applies; the engines its exercises run on (`duckdb`, `polars`, `pandas`, `delta`, `kernel`, `pipeline`, `model`) |
| `lesson`, `datasets`, `size` | Usually `lesson.md`; generated datasets it uses (`shop`); their size, `S` (1,000 orders), `M` (100,000) or `L` (2,000,000), `S` if left out |
| `exercises` | Each with `id`, `prompt`, `engine`, optional `setup` and `starter`, `solution`, `hints` (a nudge first, nearly the answer last), `checks` and, for SQL, `expected` (`columns` and `rows`) |
| `rules` | Best-practice rule ids the lesson teaches, such as `SQ002`. A finding of that rule links here |
| `docs` | Pages in this guide (`sql-best-practices.md#sq002`) or the platform's own documentation |
| `version`, `status`, `reviewed` | Bump `version` when the content changes; `draft`, `live` or `retired`; the last review date, required when live |

## Checks

Every check says in words why it passed or failed, so the learner knows what to change.

| Check | Passes when | Settings |
| --- | --- | --- |
| `result_matches` | The answer gives the reference answer: `expected` if given, else the solution's | `ordered`: the order counts too |
| `table_shape` | A table the answer makes has these columns and types, unique keys and no empty values | `table`, `columns` (`{name: type}`), `unique`, `not_null` |
| `integrity` | Every row of a child table finds its parent | `child`, `column`, `parent`, `parent_column` |
| `no_findings` | The answer has no finding of these best-practice rules | `rules` |
| `faster_than` | Same answer as the baseline, at least `factor` times faster (fastest of `runs` runs each, taking turns, on this PC) | `baseline` (a file), `factor` (2), `runs` (3) |
| `defects_found` | The answer lists (table, key) for every seeded defect | `kinds`: only these defect kinds |

## Datasets

`shop` has `customers`, `stores`, `products` and `orders`, made on this PC from a seed: the same seed
always gives the same rows. Unless a lab turns them off, six defects are put in on purpose, one of
each kind: a customer listed twice, a missing email, a region in lower case, a negative amount, an
order dated in the future, and an order from a store that doesn't exist. The lab records which rows
they are, so `defects_found` knows the answer.

## How a lab runs

A lab lives in its own folder, `learn/<id>` in the app's data folder, with a DuckDB database of its
datasets and setup tables and the learner's answers. The answer runs with no access to files or the
network, inside a transaction that is always undone, so checking it never changes the lab. The lab
code doesn't talk to Fabric or OneLake at all. **Reset** deletes the folder and starts again.

## Rules for content

- **Learn by doing.** Every module has at least one exercise with a check. Keep lessons short.
- **No raw HTML.** The app shows lessons as text. Put HTML in a code block if a lesson is about HTML.
- **Only your own folder.** File names are plain names inside the module's folder.
- **Claims need evidence.** Say "may be faster" unless an exercise measures it on the learner's PC. Label cloud costs as estimates. Name the product and version for platform-specific advice, and link its documentation.
- **Explain tradeoffs.** Where more than one approach is valid, say when each fits.
- **No secrets or real data.** Exercises use generated or made-up data.

## Checks before a lesson ships

`sidecar/tests/test_learn_content.py` runs in CI. It fails when a module names something that
doesn't exist (a rule, a prerequisite, a path, a file, a dataset, a check), when prerequisites go
round in a circle, when a live module has no review date, when a lesson has raw HTML, or when a SQL
reference solution doesn't give its `expected` answer. `test_learn_labs.py` starts every DuckDB
module as a lab and checks that each reference solution passes all of its own checks. Run them with:

```
cd sidecar
python -m pytest -q tests/test_learn_content.py tests/test_learn_labs.py
```

A reviewer also checks that the lesson is technically accurate for the platforms it names, that it
works with a keyboard and a screen reader, and that its speed and cost claims are measured or
labelled as estimates.
