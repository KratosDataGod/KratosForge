"""Ranks (PB-125): earned from verified evidence only, never a god's name, and new lessons never take one away."""

from __future__ import annotations

import base64
import re
import struct
import zlib

import pytest
from test_profile import call, plain, validation  # noqa: F401 - the signed-in service fixture
from test_profile_learn import passed

from fabric_desktop import progress, ranks
from fabric_desktop.learn import content


def module(mid, *exercises, kind=None, path="data-fundamentals"):
    return {"id": mid, "kind": kind or ("lab" if mid.startswith("LAB-") else "module"), "title": mid, "status": "live",
            "version": 1, "level": "beginner", "path": path, "exercises": [{"id": e} for e in exercises or ("x",)]}


def finish(p, m, kinds=("result_matches",), at=None):
    for ex in m["exercises"]:
        row = progress.from_lab(passed(m, ex["id"], kinds=kinds), m)
        if at:
            row["at"] = at
        progress.add(p, row)
    done = progress.module_complete(p, m)
    if done:
        if at:
            done["at"] = at
        progress.add(p, done)
    return p


LIVE = content.modules(include_drafts=False)


def test_no_rank_is_or_resembles_a_god():
    banned = re.compile(r"god|deity|divine|olympian|immortal", re.I)
    for r in ranks.RANKS:
        assert not banned.search(r["name"]) and not banned.search(r["id"]), r["name"]
        assert not banned.search(r["prompt"]), r["name"]
    assert not banned.search(ranks.PROMPT_STYLE)


def test_prompts_are_original_and_name_no_game_character():
    for r in ranks.RANKS:
        assert not re.search(r"kratos|god of war|spartan|ghost of", r["prompt"], re.I), r["name"]
    assert "original design not based on any existing video game" in ranks.PROMPT_STYLE


def test_the_ladder_keeps_the_names_people_already_see():
    names = [r["name"] for r in ranks.RANKS]
    assert len(names) == 13 == len(set(names))
    for old in ("Apprentice", "Forge Initiate", "Iron Forger", "Steel Forger", "Master Forger", "Wolf Guardian",
                "Forge Architect", "Legend of the Forge"):
        assert old in names
    assert {r["tier"] for r in ranks.RANKS} == set(ranks.TIERS)


def test_every_shipped_lesson_is_planned_under_its_own_path():
    planned = {m: p for p, v in ranks.PATHS.items() for m in v["modules"]}
    for path in content.paths():
        assert path["id"] in ranks.PATHS
        for mid in path["modules"]:
            assert (planned.get(mid) or ranks.ADDED_AFTER.get(mid)) == path["id"], mid


def test_everyone_starts_as_an_apprentice_and_one_exercise_makes_a_forge_initiate():
    s = ranks.standing(progress.empty(), LIVE)
    assert (s["rank"], s["number"], s["of"]) == ("Apprentice", 1, 13)
    assert s["next"]["name"] == "Forge Initiate" and s["next"]["checks"][0]["text"] == "Pass 1 different exercise"
    p = finish(progress.empty(), module("LM-01"))
    assert ranks.standing(p, LIVE)["rank"] == "Forge Initiate"


def test_iron_forger_needs_every_core_lesson_and_ranks_are_climbed_in_order():
    p = progress.empty()
    for mid in ("LM-01", "LM-02", "LM-03", "LM-04", "LM-05"):
        finish(p, module(mid))
    s = ranks.standing(p, LIVE)
    assert s["rank"] == "Forge Initiate" and s["next"]["checks"][0]["have"] == 5
    finish(p, module("LAB-01"))
    assert ranks.standing(p, LIVE)["rank"] == "Iron Forger"
    # Steel Forger's evidence without Iron Forger's still isn't Steel Forger
    q = finish(progress.empty(), module("LAB-02", path="sql-development"))
    progress.add(q, progress.from_validation(validation()))
    got = ranks.standing(q, LIVE)
    assert got["rank"] == "Forge Initiate" and not got["ladder"][2]["held"] and not got["ladder"][3]["held"]


def test_steel_forger_needs_a_clean_validate_run_too():
    p = progress.empty()
    for mid in ("LM-01", "LM-02", "LM-03", "LM-04", "LM-05", "LAB-01"):
        finish(p, module(mid))
    finish(p, module("LAB-02", path="sql-development"))
    assert ranks.standing(p, LIVE)["rank"] == "Iron Forger"
    progress.add(p, progress.from_validation(validation()))
    assert ranks.standing(p, LIVE)["rank"] == "Steel Forger"


def test_excluding_evidence_takes_the_rank_away():
    p = finish(progress.empty(), module("LM-01"))
    p["excluded"] = [r["id"] for r in p["evidence"]]
    assert ranks.standing(p, LIVE)["rank"] == "Apprentice"


def test_ranks_needing_unshipped_lessons_are_coming_soon_not_unreachable():
    every = [module(m, path=p) for p, v in ranks.PATHS.items() for m in v["modules"]] + [module("LAB-12")]
    assert not any(r["coming_soon"] for r in ranks.standing(progress.empty(), every)["ladder"])
    # Before Enterprise Architecture shipped, the ranks that need it were coming soon, the rest weren't.
    ea = set(ranks.PATHS["enterprise-architecture"]["modules"])
    s = {r["id"]: r for r in ranks.standing(progress.empty(), [m for m in every if m["id"] not in ea])["ladder"]}
    assert not s["iron-forger"]["coming_soon"]
    assert s["forge-architect"]["coming_soon"] and s["legend-of-the-forge"]["coming_soon"]
    # Today every lesson the ladder names has shipped.
    assert not any(r["coming_soon"] for r in ranks.standing(progress.empty(), LIVE)["ladder"])


def test_a_new_lesson_never_takes_a_held_rank_away():
    p = progress.empty()
    for mid in ("LM-01", "LM-02", "LM-03", "LM-04", "LM-05", "LAB-01"):
        finish(p, module(mid))
    assert ranks.standing(p, LIVE + [module("LM-13"), module("LM-14")])["rank"] == "Iron Forger"


def test_lessons_added_after_v1_never_join_the_rank_requirements():
    # Ladder v1 is frozen: a lesson shipped later (ADDED_AFTER) shows in its Learn path but no rank needs it,
    # so finishing the v1 Enterprise Architecture list still meets Forge Architect with LM-36 published.
    planned = {m for v in ranks.PATHS.values() for m in v["modules"]}
    assert ranks.ADDED_AFTER and not planned & set(ranks.ADDED_AFTER)
    p = progress.empty()
    for mid in ranks.PATHS["enterprise-architecture"]["modules"]:
        finish(p, module(mid, path="enterprise-architecture"))
    live = LIVE + [module(mid, path=path) for mid, path in ranks.ADDED_AFTER.items() if mid not in {m["id"] for m in LIVE}]
    architect = next(r for r in ranks.standing(p, live)["ladder"] if r["id"] == "forge-architect")
    assert architect["checks"][0]["met"] and not architect["coming_soon"]


def test_proof_forged_is_never_needed():
    needs = [n for r in ranks.RANKS for n in r["needs"]]
    assert not any("proof-forged" in n.get("achievements", []) for n in needs)


def test_every_rank_carries_its_prompt_with_the_shared_style():
    for r in ranks.standing(progress.empty(), LIVE)["ladder"]:
        assert r["prompt"].endswith(ranks.PROMPT_STYLE) and len(r["prompt"]) < 600


# ---- the service: the title is the rank, and the picture stays on this PC


def png(size=256, extra=b""):
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * size for _ in range(size))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + extra + chunk(b"IEND", b""))


def test_the_profile_title_is_the_rank_and_the_ladder_comes_with_it(plain):  # noqa: F811
    view = call(plain, "profile_get")
    assert view["progression"]["title"] == "Apprentice" == view["ranks"]["rank"]
    assert len(view["ranks"]["ladder"]) == 13 and view["avatar"] is None


def test_a_profile_picture_is_kept_and_removed(plain):  # noqa: F811
    data = png()
    view = call(plain, "profile_avatar_set", png_base64=base64.b64encode(data).decode())
    assert view["avatar"] == "data:image/png;base64," + base64.b64encode(data).decode()
    assert call(plain, "profile_avatar")["avatar"] == view["avatar"]
    assert call(plain, "profile_avatar_remove")["avatar"] is None
    assert call(plain, "profile_avatar") == {"avatar": None}


@pytest.mark.parametrize("bad", [base64.b64encode(png(128)).decode(), base64.b64encode(b"GIF89a....").decode(),
                                 base64.b64encode(png(extra=b"\x00" * (600 * 1024))).decode(), "not base64!"],
                         ids=["not-square", "not-png", "too-big", "not-base64"])  # a base64 id overflows PYTEST_CURRENT_TEST on Windows
def test_only_a_small_square_png_is_kept(plain, bad):  # noqa: F811
    with pytest.raises(Exception, match="picture"):
        call(plain, "profile_avatar_set", png_base64=bad)
    assert call(plain, "profile_avatar") == {"avatar": None}


def test_deleting_the_profile_deletes_the_picture(plain):  # noqa: F811
    call(plain, "profile_avatar_set", png_base64=base64.b64encode(png()).decode())
    call(plain, "profile_delete")
    assert call(plain, "profile_avatar") == {"avatar": None}
