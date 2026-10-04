from selene.app import merge_layers
from selene.blocks import extract_code, orbits_used, sound_names, split_statements, wrap


def test_extract_fenced():
    reply = "Here you go:\n```haskell\nd1 $ s \"bd*4\"\n```\nEnjoy"
    assert extract_code(reply) == 'd1 $ s "bd*4"'


def test_extract_unfenced_strips_prompt():
    assert extract_code('tidal> d1 $ s "bd"\n') == 'd1 $ s "bd"'


def test_split_multiple_statements_and_comments():
    code = """setcps (120/60/4)
-- drums
d1 $ stack [
  s "bd*4",
  s "hh*8"
]
  # room 0.3

d2 $ n "0 3" # s "superpiano"
"""
    stmts = split_statements(code)
    assert len(stmts) == 3
    assert stmts[0] == "setcps (120/60/4)"
    assert stmts[1].splitlines()[-2:] == ["  ]", "  # room 0.3"]
    assert stmts[2].startswith("d2")


def test_split_operator_continuation_at_column_zero():
    stmts = split_statements('d1 $ s "bd*2"\n# speed 2\n|+ n 1')
    assert stmts == ['d1 $ s "bd*2"\n  # speed 2\n  |+ n 1']


def test_wrap():
    assert wrap('d1 $ s "bd"') == ':{\nd1 $ s "bd"\n:}\n'
    assert wrap(":t sound") == ":t sound\n"


def test_orbits_and_sounds():
    code = 'd2 $ s "bd:3 ~ [sn cp]*2"\nd1 $ sound "<superpiano arpy>" # n "0 7"'
    assert orbits_used(code) == ["d1", "d2"]
    assert sound_names(code) == {"bd", "sn", "cp", "superpiano", "arpy"}
    assert sound_names('s "808oh*2 808cy(3,8,2) ~ bd:3?"') == {"808oh", "808cy", "bd"}


def test_merge_layers_replaces_orbit():
    playing = 'd1 $ s "bd*4"\nd2 $ s "hh*8"'
    assert merge_layers(playing, 'd2 $ s "cp"') == 'd1 $ s "bd*4"\nd2 $ s "cp"'


def test_sounding_orbits_ignores_silence():
    from selene.blocks import sounding_orbits
    assert sounding_orbits('d1 $ s "bd"\nd3 $ silence\nd4 silence') == ["d1"]


def test_fix_message_adds_hint():
    from selene.llm import fix_message
    msg = fix_message("• Probable cause: ‘every’ is applied to too few arguments")
    assert "Move it in front of the pattern" in msg


def test_flowify_wraps_orbits_only():
    from selene.blocks import flowify
    code = 'setcps 0.5\nd1 $ s "bd*4" # gain 0.9 -- kick\nd2 silence\nd3 $ silence'
    out = flowify(code).split("\n")
    assert out[0] == "setcps 0.5"
    assert out[1] == 'd1 $ degradeBy (cF 0 "fl_thin1") $ (s "bd*4" # gain 0.9 -- kick'
    assert out[2].startswith('  ) |* gain (cF 1 "fl_gain1")')
    assert out[3:] == ["d2 silence", "d3 $ silence"]


def test_as_xfade_and_statement_for():
    from selene.blocks import as_xfade, statement_for
    assert as_xfade('d3 $ s "cp*2"', 8) == 'xfadeIn 3 8 $ (s "cp*2"\n  ) |< orbit 2'
    reply = 'setcps 1\nd1 $ s "bd"\nd3 $ n "0 2"\n  # s "superpiano"'
    assert statement_for(reply, "d3") == 'd3 $ n "0 2"\n  # s "superpiano"'
    assert statement_for(reply, "d2") is None


def test_user_message_marks_unplayed_edits():
    from selene.llm import user_message
    msg = user_message("add hats", 'd1 $ s "bd"', 'd1 $ s "bd*2"')
    assert "not played it yet" in msg and 'd1 $ s "bd*2"' in msg and 'd1 $ s "bd"' in msg
    assert "edited" not in user_message("add hats", 'd1 $ s "bd"')
