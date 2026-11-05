"""Fabric App Studio (Preview): the canonical application specification, its patch operations and the
project folder that saves it. The design is in docs/architecture/app-studio.md.

Nothing here calls a model or Fabric. The specification is the source of truth: an AI edit and a visual
edit both become the same patch operations, and a project opens, validates and saves without any model.
"""
