"""Optimize: optimization results shared across Fabric Desktop installations (PR-OPT-001 onwards).

An organization keeps one central lakehouse (default name ``FabricDesktop_Central``), or several
when it needs separate boundaries. Every installation that connects to it writes its own rows into
the same ``fdopt_*`` Delta tables and reads everyone's; other features add their own table families
beside them (schema.register). Design and reasoning:
docs/optimize/README.md.

- ``schema``        the tables, their columns and the migrations that build them
- ``lakehouse``     where the tables live: a lakehouse in OneLake, or a folder on this PC for tests
- ``setup``         inspect, initialize, upgrade and check access to a lakehouse
- ``installation``  this installation's random id
- ``registry``      the lakehouses this PC is connected to, and which one is active
"""
