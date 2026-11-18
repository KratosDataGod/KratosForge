"""Notebook versions at their exact edges (boundary re-score of batch 4, mutation batch 160,
docs/quality/mutation-testing.md): two versions kept in the same instant."""

from __future__ import annotations

from fabric_desktop import notebook_versions as V


def _nb(source):
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
            "cells": [{"cell_type": "code", "metadata": {}, "source": source, "outputs": [], "execution_count": None}]}


def test_a_checkpoint_in_the_same_instant_as_a_save_comes_after_it(tmp_path):
    V.record(tmp_path, "NB", _nb("x = 1"), now=1000.0)
    V.record(tmp_path, "NB", _nb("x = 2"), kind="checkpoint", label="named", now=1000.0)
    (newest, older) = V._entries(tmp_path, "NB")
    assert newest["kind"] == "checkpoint" and newest["t"] == 1000.001 and older["t"] == 1000.0
