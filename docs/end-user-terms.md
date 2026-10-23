# End-user terms (draft)

> **Draft, not in effect.** These are proposed terms for KratosForge, written so that the app may include Microsoft's SQL driver. KratosBI's counsel must review them before they are used. Until then, nothing in the app shows them or asks anyone to accept them, and they bind no one. Text in [brackets] is still to be decided.

## Why these terms are needed

The engine includes Microsoft's SQL driver (`mssql-python`, MIT licence). It brings two Microsoft components that come under Microsoft's own licence terms, not open-source ones:

- **Microsoft ODBC Driver 18 for SQL Server:** `msodbcsql18.dll`, `msodbcdiag18.dll` and `msodbcsqlr18.rll`.
- **Microsoft Visual C++ runtime:** `msvcp140.dll`.

Their licence files are in the engine at `Lib\site-packages\mssql_python_odbc\libs\windows\x64\` ([Install](install.md)). Section 2 of the ODBC Driver 18 licence lets an application redistribute the driver on three conditions:

1. The application adds significant primary functionality to it.
2. Its distributors and end users agree to terms that protect the driver and Microsoft at least as much as Microsoft's licence does.
3. The distributor indemnifies Microsoft against claims arising from the distribution or use of the application.

Sections 5 and 6 below are written to meet condition 2. Condition 3 is an undertaking KratosBI gives Microsoft by distributing the app, so it doesn't belong in terms that users accept. It still needs counsel's sign-off.

## The terms

**1. Who these terms are between.** These terms are an agreement between you and [KratosBI legal entity name] ("KratosBI"). They cover KratosForge, the desktop app and its engine, and the updates KratosBI provides for it (together, "the software"). By installing or using the software, you accept these terms. If you don't accept them, don't install or use it.

**2. What you may do.** You may install and use any number of copies of the software, on computers you control, for your organisation's work. [Commercial terms, such as seats, subscription and trial, to be decided.]

**3. What stays yours, and what stays ours.** The data you work on and the files you create stay yours. KratosBI keeps all rights in the software that these terms don't expressly give you.

**4. Microsoft Fabric and other services.** The software works with Microsoft Fabric, OneLake and other Microsoft services as you, with your own sign-in. Those services come under your organisation's agreements with Microsoft, not these terms. Some actions use your Fabric capacity; the app says which before they run.

**5. Third-party components.** The software includes open-source and third-party components. Each one comes under its own licence. The software bill of materials published with each Preview release lists them, and their licence files ship inside the engine. Where a component's licence gives you rights these terms don't, its licence applies to that component.

**6. Microsoft components.** The software includes Microsoft ODBC Driver 18 for SQL Server and the Microsoft Visual C++ runtime ("the Microsoft components"). Microsoft, not KratosBI, licenses them. You may use them only as part of the software. You may not:

- (a) separate them from the software, or share, publish, distribute, lend or transfer them on their own;
- (b) work around any technical limitations in them;
- (c) reverse engineer, decompile or disassemble them, except where applicable law allows it despite this limitation;
- (d) remove, minimise, block or modify any notices of Microsoft or its suppliers in them;
- (e) use them in any way that is against the law, or to create or spread malware;
- (f) provide them as a stand-alone hosted solution for others to use;
- (g) use Microsoft's trademarks in a way that suggests the software comes from Microsoft or is endorsed by it.

The Microsoft components are provided "as is". Microsoft gives no warranty for them and has no obligation to support them. To the extent applicable law allows, Microsoft is not liable to you for any damages arising from them. You must comply with all export laws and regulations that apply to the Microsoft components. Their licence terms are in the engine as described above. [Counsel: confirm whether Microsoft should be named as a third-party beneficiary of this section.]

**7. Usage data.** The software sends no usage data unless you, or your organisation, turn it on. [Usage data](telemetry.md) lists every field it sends and what is never sent.

**8. Updates.** The software can check for and install updates from KratosBI. These terms apply to updates unless an update comes with different terms.

**9. No warranty.** The software is provided "as is", without warranties of any kind, to the extent applicable law allows. [Counsel to finalise.]

**10. Limitation of liability.** [Counsel to finalise.]

**11. Governing law.** [To be decided.]

## Before these terms take effect

- Counsel reviews and finalises the terms, including the indemnity KratosBI gives Microsoft.
- The app shows the terms and records acceptance: on first run, and in the installer and the Enterprise install docs for managed rollouts. This needs a mockup first.
- These terms are published before the first Stable release that includes the warehouse driver.
