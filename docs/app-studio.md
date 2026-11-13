# App Studio

App Studio is in preview. Open it from **Create > Applications > App Studio**, or search for "App Studio" with Ctrl+K. Settings can hide it with the other preview features.

App Studio builds an application from a specification: its pages, components, measures, filters and theme, kept as one `app.json` file. Design changes that file through small changes it can undo, and every project keeps its history and its versions. The design behind it is in [Fabric App Studio](architecture/app-studio.md).

## Create, open and import

- **Create an app**: give it a name, choose where it will run (a Power BI report, or a KratosForge app) and pick a template. The templates read sample data, so a new app previews at once.
- **Open an app**: every project on this PC, newest change first.
- **Import…**: an App Studio export (.zip) becomes a new project. Only the project's own files are taken, and they are checked for keys and passwords first.

Projects are folders under this PC's KratosForge data (`appstudio/projects/<name>.KFApp`).

## The workspace

- **Preview** draws the page on this PC with sample numbers. It is labelled as sample data: it shows the layout and how a slicer narrows the page, not your figures.
- **Design** changes the page: titles, what each component shows, its measure and width, adding and removing components and pages.
- **Spec** shows the specification, and any problems with it.
- **History** lists the versions you saved and every change. **Restore** keeps what's there now as a new version first.
- **Undo** and **Redo** step through changes. **Save version** keeps the app as it is. **More** has Save As (with history), Clone (without) and Export to Downloads.

## Model connections

**Model connections** (on App Studio's first page) is where you add your own AI model: Anthropic, OpenAI, Azure OpenAI, Gemini, Ollama on this PC, or an OpenAI-compatible server.

- A connection keeps where your key is, never the key: a Windows Credential Manager entry (`credman:KratosForge/<name>`, a Generic credential with the key as its password) or an environment variable (`env:KRATOSFORGE_<NAME>`, or the provider's own name such as `env:ANTHROPIC_API_KEY`). Ollama needs no key.
- **What this model may be sent** is your consent. Nothing is sent until you allow app context: your descriptions, the app's pages and components, and the semantic model's names, types and descriptions, with emails, tenant and site names and ids taken out first. Never rows. Measure formulas (DAX) go only when you tick them for one request, because a formula can hold literal business values such as a customer name or a price.
- **Spend limit**: with a monthly limit and prices set, a request that would pass the limit is refused, and you're warned at 80 %.
- **Test** reads the key and lists the models it can see. It spends no tokens and sends no app or data.
- Your organization can turn connections off, limit the providers and the addresses they reach, and stop sample rows being sent (the `appstudio_*` keys in the [policy file](azure-setup.md)). A connection the policy stops says why.

## Publish

**Publish…** makes the app a Power BI report in a Fabric workspace.

- It checks first, and the check writes nothing. It reads the app's semantic model, the workspace's name and the reports in it, and stops on anything the model doesn't have (a measure or a column), and on an app that still reads sample data. Anything Power BI will show differently (visual titles and theme colours aren't carried over yet) is listed.
- You choose the workspace (the model's own by default) and the report's name, then type the workspace's name to publish. One report is made, bound to that semantic model. Nothing else in Fabric changes.
- If a report with that name is already there, you're told whether this project published it, and it's replaced only when you tick **Replace that report**.
- If the app, the workspace or a report there changed after the check, you're shown the new check and asked again.
- Each attempt goes in the app's **History** with the workspace and report ids, never the definition or a token. If a publish fails part way, History says so, and a report it left behind is found and offered for replacing next time.
- When it's done, **Open in Power BI** or **Open in Embed Studio** shows the report.

Building and previewing use no capacity; viewing the published report uses the workspace's.

## What comes next

- **Describe an app** (the engine is ready; its screen comes next): your model gets your description and the semantic model's names, types and descriptions, never rows, and answers with a plan (pages and what each shows). KratosForge keeps only what the data and the target support, lists what it left out, and makes the app from the plan you approve. The model never writes the app itself.
- **Ask for a change** (engine ready, screen next): your model sees the app's pages and components and the same metadata, and answers with named changes such as "show this as a column chart" or "add a card". KratosForge checks them against the data and the target, shows the result before anything is saved, and saves your approval as one step that Undo takes back.
