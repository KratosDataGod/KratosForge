# Notebook magics and cell languages

Magic commands are lines that start with `%` (one line) or `%%` (a whole cell). KratosForge
runs the ones Fabric and Databricks notebooks have where they make sense on your PC, and says so
plainly where they don't. This page grows as more of them land; [Notebooks and SQL](notebooks.md)
covers the rest of the notebook.

## SQL results in Python

A `%%sql` cell shows its result as a table, as in Fabric. It also keeps that result in a variable
called `_sqldf`, as Databricks does, so the next Python cell can carry on from it:

```sql
%%sql
SELECT region, sum(amount) AS total FROM dbo.sales GROUP BY region
```

```python
top = _sqldf.orderBy("total", ascending=False).limit(3)
display(top)
```

- `_sqldf` is a `spark` DataFrame. It shows in the **Variables** pane.
- Only a query sets it (`SELECT`, `WITH`, `SHOW`, `DESCRIBE`...). A cell that creates a view or
  writes a table leaves the previous `_sqldf` as it was.
- To run one query inside a Python cell, put `%sql` in front of it. Assign it to keep the result,
  or put it on the cell's last line to show it:

```python
df = %sql SELECT * FROM dbo.sales WHERE region = 'West'
print(df.count())
%sql SELECT max(amount) AS largest FROM dbo.sales
```

`MERGE`, `UPDATE` and `DELETE` change rows in place, which runs in Fabric only, so `%sql` stops
with an error for them and **Runs on** lists the cell as Fabric only. Put them in a `%%sql` cell to
run them once the notebook is published.

**In Fabric.** Fabric notebooks have `%%sql` cells but neither `%sql` lines nor `_sqldf`. When
you publish, KratosForge writes those as calls Fabric has: `df = %sql ...` becomes
`df = spark.sql("...")` (`lakehouse.sql` in a Python notebook), and a `%%sql` cell whose `_sqldf`
a later cell uses becomes `_sqldf = spark.sql("...")` followed by `display(_sqldf)`. Your notebook
on this PC keeps the magics.

Dependencies, stale cells, Validate and Prove read `%sql` lines too: a cell that uses `_sqldf`
depends on the `%%sql` cell before it, the tables a `%sql` line reads count as reads, and a `%sql`
line that writes a table is refused in a proof, like a `%%sql` cell.

## Cell languages

A cell's first line can say what language the rest of it is in. Fabric writes `%%name`;
Databricks writes `%name`. Both work, so a notebook brought over from Databricks runs without
editing its headers.

| First line | Fabric | Databricks | On this PC |
| --- | --- | --- | --- |
| Spark SQL | `%%sql` | `%sql` (alone on the line) | Runs through the notebook's `spark`; the result is kept in `_sqldf` |
| Python | `%%pyspark`, `%%python` | `%python` | Runs as Python; line numbers in errors match the cell |
| Markdown | `%%markdown` | `%md` | Shows the text as Markdown under the cell |
| HTML | `%%html` | | Shows the HTML in a sandboxed frame |
| Scala | `%%spark` | `%scala` | Needs Fabric's Spark: switch **Runs on** to Fabric |
| R | `%%sparkr` | `%r` | Needs Fabric's Spark: switch **Runs on** to Fabric |
| .NET | `%%csharp` | | Needs Fabric's Spark: switch **Runs on** to Fabric |

`%sql` followed by a query on the same line is the one-line `%sql` above, not a header.

The editor highlights a `%sql` cell as SQL and a `%md` cell as Markdown, and the cell's language
picker shows Spark SQL for both `%%sql` and `%sql`. Dependencies, Validate, Prove and the
**Runs on** check read the header too, so a `%sql` cell's tables count as reads and a `%scala`
cell is listed as Fabric only.

**In Fabric.** Publishing writes Databricks headers the way Fabric reads them: `%sql` becomes
`%%sql`, `%scala` becomes `%%spark`, `%r` becomes `%%sparkr`, `%python` is dropped, and a `%md` or
`%%markdown` cell becomes a Markdown cell. Your notebook on this PC keeps what you wrote.

## Shell commands and files

A line that starts with `!` runs in this PC's shell, as in IPython, Fabric and Databricks
notebooks. Output appears in the cell as it comes, and **Cancel** ends the command and everything
it started.

```python
!pip list
files = !dir /b Files
print(len(files))
```

- `x = !command` keeps the output as a list of lines instead of showing it.
- `{expression}` and `$name` take values from the notebook (`!echo {table_name}`); `$$` is a
  plain `$`. Text in braces that isn't Python, such as `find . -exec rm {} ;`, is left alone.
- `_exit_code` holds the last command's exit code. A failing `!` command doesn't fail the cell,
  as in IPython.
- `!` uses `cmd.exe` on Windows. Fabric runs on Linux, so a notebook you publish needs Linux
  commands there.

**A notebook's `!` lines and shell cells run on your PC, as you.** Running a notebook someone
else wrote runs their shell commands here, with your files and your sign-in, the same as running
a script they sent you. Read it first. See [Security](security.md), item 6.

A whole cell can be a script:

| First line | Runs in |
| --- | --- |
| `%sh` (Databricks), `%%sh` | bash or sh. On Windows, the one Git for Windows or WSL installs |
| `%%bash` | bash |
| `%%cmd` | Windows' command prompt |
| `%%powershell` | PowerShell |

A script cell fails when its last command exits with a non-zero code, as a `%sh` cell does in
Databricks. `%%cmd` and `%%powershell` cells are listed as not running in Fabric by the **Runs
on** check; a `%sh` cell is published as `%%sh`.

`%fs` runs one `notebookutils.fs` command, as in Databricks, with the same paths and the same
rules (writes to OneLake need a lakehouse you can write to, and a proof refuses them):

| Command | Does |
| --- | --- |
| `%fs ls Files/raw` | Lists a folder as a table (name, path, size, isDir) |
| `%fs head Files/raw/a.csv` | Prints the start of a file |
| `%fs cp [-r] from to` | Copies a file, or a folder with `-r` |
| `%fs mv from to` | Moves a file or folder; the source goes only once the copy is complete |
| `%fs rm [-r] path` | Removes a file, or a folder with `-r` |
| `%fs mkdirs path` | Makes a folder |
| `%fs put [-f] path "text"` | Writes text to a file; `-f` replaces one that exists |

Dependencies treat shell commands and `%fs` changes as side effects, so **Run stale cells** asks
before running them again, and Prove warns that it can't contain what a shell command changes.

## IPython magics

The everyday IPython magics that Fabric's and Databricks' notebooks inherit work here too.
`%lsmagic` lists every magic this PC runs.
Put a path with spaces in quotes, like `%%writefile "C:\Users\Chris Smith\helpers.py"`; backslashes in
Windows paths are kept as they are. Cells and files saved with Windows line endings or a byte order mark
(Notepad's UTF-8) read the same as any other.

| Magic | Does |
| --- | --- |
| `%time statement`, `%%time` | Runs it and prints the wall time |
| `%timeit expression`, `%%timeit` | Runs it many times and prints the mean and spread per loop, as IPython does. `-n 100` sets loops per run and `-r 5` runs; by default it picks enough loops for 0.2 seconds and does 7 runs |
| `%%capture name` | Keeps the cell's printed output, tables and charts in `name` instead of showing them. `name.stdout` is the text; `name.show()` shows it all. An error still shows |
| `%%writefile path` | Writes the cell's text to a file on this PC, replacing a file that's there without asking; `-a` appends. A relative path lands in the engine's working folder (`%pwd`), not the notebook's; give a full path to choose where |
| `%env`, `%env NAME`, `%env NAME=value` | Lists, reads or sets environment variables. Values of names that look secret (token, key, password...) show as hidden. `FABRIC_DESKTOP_*` and the proxy and certificate settings (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`, `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE` and similar) can't be set here, because the engine's signed-in Fabric calls use them |
| `%who`, `%whos` | Lists the notebook's variables (the same ones as the **Variables** pane) |
| `%pwd` | The kernel's working folder |
| `%reset -f` | Clears every variable and `Out`; `spark`, `notebookutils` and `display` stay |
| `In`, `Out`, `_`, `__`, `___`, `_3`, `_i`, `_i3` | The notebook's history, as in IPython: `In[3]` and `_i3` are cell 3's code, `Out[3]` and `_3` its result, `_` the last result. `_i` is the cell before this one. A notebook run with `%run` isn't recorded, `Out` keeps the last 20 results (as many tables as the result viewer keeps), and Restart starts again from 1 |
| `%history`, `%history -n 4-6`, `%history -l 3 -o` | Prints earlier cells' code, oldest first. `-n` numbers them, `-o` adds each result, `-l N` keeps the last N; ranges are cell numbers |
| `%matplotlib inline` | Accepted: charts always show in the cell |
| `%load_ext autoreload`, `%autoreload 2`, `%aimport name` | Reloads your own modules when you edit their files, before the next cell runs; see below |
| `%load_ext` (other extensions), `%config` | Skipped with a note: they change IPython's own shell, and this kernel isn't IPython |

### Editing your own modules: %autoreload

Keep helper code in a `.py` file (for example in the notebook's resources, or a folder on
`sys.path`), import it, and turn on autoreload. When you edit the file, the next cell uses the new
code without restarting the session:

```python
%load_ext autoreload
%autoreload 2
from helpers import clean_names    # after you edit helpers.py, clean_names is the new one
```

- `%autoreload 2` (or `all`, or `3`/`complete`) reloads every module that is your own file;
  `%autoreload 1` (`explicit`) only those named with `%aimport helpers`; `%autoreload 0` (`off`)
  stops. `%autoreload` alone reloads changed modules now. `%aimport -name` leaves one out, and
  `%aimport` lists both.
- Only your own files reload: Python's own modules, installed packages and KratosForge's engine
  never do.
- Names taken with `from helpers import f` follow the new code. Objects made before the edit keep
  their old class, so make them again if their class changed.
- A file that fails to reload (a syntax error) is reported above the cell's output, and the cell
  runs with the module as it was.

Two aren't available, and say why when used: `%cd`, because the kernel shares its working folder
with the app, and `%%javascript`, because cell output shows in a sandboxed frame without scripts.

Dependencies treat a `%%writefile` cell as writing that file and a `%%capture name` cell as
defining `name`.

## Running another notebook with %run

`%run` runs another notebook in this one, so its functions and variables are yours afterwards.
Both products' ways of passing values work:

```python
%run Load sales {"day": "2026-01-01", "full": true}
%run ./etl/Load sales $day="2026-01-01" $full="true"
```

- Fabric's form takes a JSON object (or a Python dict). Databricks' form takes `$name=value`
  pairs, whose values are always text, as Databricks widgets are.
- The values replace those in the other notebook's **parameter cell**; without one, they are set
  as variables before it runs.
- A name can have spaces, and can be quoted. A folder path in front (`./`, `../shared/`) and an
  `.ipynb` ending are dropped: notebooks are found by name.

### Running a file from the notebook's resources: %run -b

`%run -b file.py` (or `--builtin`) runs a Python file from the notebook's
[Built-in resources](notebook-resources.md) in this session, as in Fabric, so its functions and
variables are yours afterwards. `%run -b file.sql` runs a SQL file as a `%%sql` cell would, and
leaves a query's result in `_sqldf`.

```python
%run -b helpers.py
%run -b helpers.py {"factor": 2}     # set as variables before the file runs
%run -b checks/row_counts.sql
```

- The file must be in this notebook's resources; a path that climbs out of them (`../`) is not
  followed.
- `-c` (`--current`) works the same in the notebook you run. Inside a notebook that another one
  runs with `%run`, `-c` stops with a message, because this PC doesn't yet track that notebook's
  own resources; use `-b`, which reads the resources of the notebook you run, as Fabric does.

## %%configure

`%%configure` sets Fabric's Spark session (driver and executor sizes, Spark `conf`, the default
lakehouse...). This PC has no Spark session to size, so running the cell checks it instead:

- The JSON must be valid, as Fabric requires; a mistake fails the cell with the line and column,
  and the **Runs on** check lists the cell as one Fabric can't read.
- The report lists each setting and what it does in Fabric: sizes apply to Fabric's nodes (the
  limits in **Settings** apply here), `conf` keys go to Fabric's Spark, and `defaultLakehouse` says
  whether the same lakehouse is attached here.
- A setting Fabric doesn't know, such as a misspelled `driverMemroy`, is named, because names
  are case-sensitive and a typo is otherwise ignored until the Fabric run.

## T-SQL cells (%%tsql)

In a Fabric Python notebook, `%%tsql -artifact WH_Sales -type Warehouse` runs T-SQL in a Fabric
Warehouse, SQL analytics endpoint (`-type Lakehouse`) or SQL database (`-type SQLDatabase`).
Those engines are in Fabric, so on this PC the cell is checked instead of run:

- The T-SQL is read against that engine's rules, the same check the SQL query page runs, and the
  cell lists what Fabric wouldn't accept (an `nvarchar` column in a Warehouse table, say). Nothing
  is sent to Fabric.
- The cell then stops, and the **Runs on** check lists it as Fabric only. Switch **Runs on** to
  Fabric to run it there, where reading the Warehouse uses capacity.

## Supported magics

| Magic | On this PC |
| --- | --- |
| `%%sql` | Spark SQL through the notebook's `spark` session; the result is shown and kept in `_sqldf` |
| `%sql` | One Spark SQL query inside Python; returns a DataFrame |
| `%%html` | Shows the HTML in a sandboxed frame |
| `%run Other notebook {"x": 1}`, `%run ./Other $x="1"` | Runs another notebook in this one's namespace, with parameters |
| `%run -b file.py`, `%run -b file.sql` | Runs a file from the notebook's Built-in resources in this session |
| `!command`, `x = !command` | Runs in this PC's shell; the assigned form keeps the output lines |
| `%sh`, `%%sh`, `%%bash`, `%%cmd`, `%%powershell` | Runs the cell as a script on this PC |
| `%fs command` | One `notebookutils.fs` command: ls, head, cp, mv, rm, mkdirs, put |
| `%time`, `%%time`, `%timeit`, `%%timeit` | Times a statement or the cell |
| `%%capture`, `%%writefile`, `%env`, `%who`, `%whos`, `%pwd`, `%reset -f`, `%lsmagic`, `%history`, `In`/`Out` | As in IPython; see above |
| `%%pyspark`, `%%python`, `%python` | A Python cell |
| `%md`, `%%markdown` | A Markdown cell's text, shown under the cell |
| `%pip install`, `%pip list`/`freeze`/`show` | Installs into the notebook's own environment (needs Run notebooks in their own process); see [Installing packages](#installing-packages-with-pip) |
| `%conda` | Not on this PC; use `%pip install` |
| `%%tsql` | Checked against the Warehouse, endpoint or SQL database rules; runs in Fabric |
| `%%configure` | Checks the JSON as Fabric would and says what each setting does there; changes nothing here |
| `%%spark`, `%%sparkr`, `%%csharp`, `%scala`, `%r` | Need Fabric's Spark; switch **Runs on** to Fabric |

## Installing packages with %pip

`%pip install` in a cell installs packages into the notebook's own
[environment](notebooks.md#notebook-environments-preview), never into the app's own Python, and the cell waits
until pip finishes:

```python
%pip install openpyxl==3.1.5 rich
%pip install -r builtin/requirements.txt    # a file in the notebook's resources
```

- It needs **Run notebooks in their own process** (Settings > Preview features). Without it the
  line is skipped with a note.
- A notebook without an environment gets one named after it, attached to it. That session started
  without it, so the cell says to restart the session before importing. Later installs import
  at once; a package the session already imported needs a restart for the new version.
- Packages come from PyPI by name, with versions as pip writes them. Paths, URLs, wheels,
  `--index-url` and `-e` are refused, and so is a requirements file that uses them. The engine's
  own packages (polars, DuckDB...) keep their versions.
- Your organization can turn installs off; `%pip install` then says so.
- `%pip list`, `freeze` and `show` read what this session can import. `%pip uninstall` is on the
  notebook's Environment page.

