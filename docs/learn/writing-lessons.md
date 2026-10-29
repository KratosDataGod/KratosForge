# Writing a Learn lesson (Preview)

KratosForge Learn is built from lessons and labs that ship inside the app. They run on this PC,
work offline, and never publish anything to Fabric. This page is for people who write or review
them. The plan is in issue #1065 and the stories it links to.

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
| `lesson`, `datasets` | Usually `lesson.md`; generated datasets it uses (the lab runner adds them) |
| `exercises` | Each with `id`, `prompt`, `engine`, optional `setup` and `starter`, `solution`, `checks` and, for SQL, `expected` (`columns` and `rows`) |
| `rules` | Best-practice rule ids the lesson teaches, such as `SQ002`. A finding of that rule links here |
| `docs` | Pages in this guide (`sql-best-practices.md#sq002`) or the platform's own documentation |
| `version`, `status`, `reviewed` | Bump `version` when the content changes; `draft`, `live` or `retired`; the last review date, required when live |

A check is one of `result_matches` (with `ordered` when order matters), `table_shape`,
`integrity`, `no_findings` (with `rules`), `faster_than` and `defects_found`.

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
reference solution doesn't give its `expected` answer. Run it with:

```
cd sidecar
python -m pytest -q tests/test_learn_content.py
```

A reviewer also checks that the lesson is technically accurate for the platforms it names, that it
works with a keyboard and a screen reader, and that its speed and cost claims are measured or
labelled as estimates.
