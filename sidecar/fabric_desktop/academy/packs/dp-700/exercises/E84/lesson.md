# Five shortcuts: type, location, identity and cache

The Marketing team wants web events, a finance ledger, ad exports and survey files without copying any of them. One table is also needed in a KQL database. An old shortcut to last year's ad exports has to go, and the files behind it must stay.

## What you'll do

Set up five shortcuts to OneLake, ADLS Gen2, Amazon S3 and Google Cloud Storage, in a lakehouse and a KQL database, with the right location, identity and cache, and retire an old shortcut without losing its data.

## How it runs here

This lab is a model, not Fabric. shortcuts.json holds what each shortcut needs and the settings you choose for it. Check my work reads it as data. Run lists every value so you can see what the checks read.

## What this run proves

- Your shortcuts follow the OneLake shortcuts page on where each kind can go (top of Tables, any level of Files, the Shortcuts folder of a KQL database), which names are recognized as tables, which identity each type uses, the cache retention range and what deleting a shortcut removes, checked against a model on this PC.

## What it doesn't prove

- That any shortcut is created or any data read in Fabric. Nothing is created here.
- The identity of the Google Cloud Storage shortcut. The shortcuts page only names the identity for OneLake, ADLS and S3 shortcuts.
- The cloud connection itself, its credential type and who has permission on it.
- Workspace settings such as turning on the shortcut cache, and OneLake security roles on the target.
