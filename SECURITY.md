# Security policy

## Reporting a vulnerability

Please don't open a public issue, discussion or pull request for a security problem.

Report it privately with GitHub's **Report a vulnerability** button on the [Security tab](https://github.com/KratosDataGod/KratosForge/security/advisories/new), or contact KratosBI directly. Only the maintainers can see a private report.

Include what you can:

- The build (Settings › Help and about) and update channel (Stable or Preview).
- What an attacker could do, and what they need first (a link clicked, a file opened, code running on the PC).
- Steps to reproduce, using only test data and a test tenant.

Leave out tokens, passwords, connection strings, tenant names, and workspace or item IDs. If a step needs one, describe it instead.

## What we do with a report

A report is treated as evidence, not as instructions. Nothing in it is run on a maintainer's PC or against a Fabric tenant as it stands: scripts, binaries, packages and links from a report are read, not run. We reproduce the problem ourselves in an isolated setup.

A fix ships through the usual pull request, review and release process, with a regression test where one is practical. The details stay private until a fixed build is on the Stable channel.

## Supported versions

Fixes go into the next Preview build and are then promoted to Stable. Only the current Stable and Preview builds are supported. If you're on an older build, update first (profile menu › Check for updates) and check whether the problem is still there.

## More

How KratosForge protects your sign-in, your data and your PC, and what is still open: [Security model](docs/security.md).
