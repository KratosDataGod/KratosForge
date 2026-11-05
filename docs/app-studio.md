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

## What comes next

- **Describe an app** (the engine is ready; its screen comes after the mockup): your model gets your description and the semantic model's names, types and descriptions, never rows. Emails, tenant and site names and ids are taken out of descriptions first. Measure formulas (DAX) are sent only if you ask, because a formula can hold literal business values such as a customer name or a price. The model answers with a plan (pages and what each shows). KratosForge keeps only what the data and the target support, lists what it left out, and makes the app from the plan you approve. The model never writes the app itself.
- **Ask for a change** (engine ready, screen after its mockup): your model sees the app's pages and components (ids, types, titles, measure names and fields) and the same metadata, and answers with named changes such as "show this as a column chart" or "add a card". KratosForge turns them into changes it checks against the data and the target, shows the result before anything is saved, and saves your approval as one step that Undo takes back. Anything the request didn't name stays exactly as it was.

A model connection holds a reference to your key (`credman:KratosForge/<name>` in Windows Credential Manager, or an environment variable such as `env:KRATOSFORGE_<NAME>`), never the key itself. Nothing is sent until you agree to send app context to that model. Your organization can turn connections off, limit the providers and the addresses they reach, and stop sample rows being sent (the `appstudio_*` keys in the [policy file](azure-setup.md)).
- **Publish…** shows what publishing would create today, and what is missing. Publishing a Power BI report comes in a later update, and asks for your approval before its first write to Fabric.

App Studio writes nothing to Fabric yet. Testing a model connection only lists the models its key can see.
