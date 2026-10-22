# Format code in a notebook

Format code lays a cell's code out in one consistent style, as **Format cell** and **Format
document** do in Fabric and Databricks notebooks. It changes only the text in the notebook on this
PC: nothing runs, and nothing is written to OneLake.

## Format one cell or the whole notebook

- **Shift+Alt+F** while you edit a code cell formats that cell. It's the same key that formats SQL
  on the SQL query page.
- **...** on a code cell, then **Format cell**.
- **Edit > Format notebook** formats every code cell. A message says how many cells changed.

**Ctrl+Z** in a cell undoes its formatting.

## What it does to each cell

| Cell | What happens |
| --- | --- |
| Python | Laid out in [Black](https://black.readthedocs.io/)'s style by [ruff](https://docs.astral.sh/ruff/formatter/), with lines up to 88 characters and double quotes. The style is the same on every PC: a `pyproject.toml` or `ruff.toml` file doesn't change it. |
| `%%pyspark` or `%python` | Formatted as Python. The magic line stays. |
| Line magics and shell lines in Python (`%time`, `%run`, `!pip`) | Kept exactly as written; the Python around them is formatted. |
| `%%sql` or `%sql` | The SQL is laid out as **Format SQL** does on the SQL query page, in Spark SQL: one clause per line, keywords in capitals. The magic line stays. When the formatter can't show that the result means the same, the cell is left as written and the message says why. |
| Markdown, `%%html`, `%md`, `%sh` and other languages, and cells that start with another cell magic such as `%%time` | Left as written. |
| Frozen cells | Left as written. Unfreeze a cell to format it. |
| A cell Python can't read | Left as written. The message names the line and what's wrong, and **Show cell** goes to it. The other cells are still formatted. |

If you type in a cell while it's being formatted, what you typed is kept and that cell isn't
changed.

## How it works

The engine runs ruff's formatter, which ships with the app, once per cell and gives each cell to it
on its own. SQL cells use the same formatter as the SQL query page (sqlglot). Neither runs your
code or reads your data.
