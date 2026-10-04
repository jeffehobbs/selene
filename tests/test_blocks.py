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
    assert out[1].startswith('d1 $ fast (toRational <$> cF 1 "fl_rate1")')
    assert out[1].endswith('$ (s "bd*4" # gain 0.9 -- kick')
    assert out[2].startswith('  ) |* gain (cF 1 "fl_gain1") |* gain (cF 1 "fl_gate1")')
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


def test_fix_message_spots_bare_negative_arguments():
    from selene.llm import fix_message
    assert "(-0.8)" in fix_message("error", 'd1 $ s "bd" # pan (range -0.8 0.8 sine)')
    assert "(-0.8)" not in fix_message("error", 'd1 $ s "bd" # pan (range (-0.8) 0.8 sine)')
    assert "(-0.8)" not in fix_message("error", 'd1 $ n "0 -1"')


def _stream(reply, sizes):
    """Feed a reply in chunks of the given sizes, cycling; collect lines."""
    from selene.blocks import CodeStream
    stream, lines, i, k = CodeStream(), [], 0, 0
    while i < len(reply):
        n = sizes[k % len(sizes)]
        lines += stream.feed(reply[i:i + n])
        i, k = i + n, k + 1
    return lines + stream.finish()


def test_code_stream_matches_extract_code_however_it_is_chunked():
    from selene.blocks import extract_code
    replies = [
        'Here you go:\n```haskell\nsetcps (120/60/4)\n\nd1 $ s "bd*4"\n  # gain 1\n```\nEnjoy!',
        '```haskell\n\nd1 $ s "bd"\nd2 $ n "0 3" # s "superpiano"\n```',
        'd1 $ s "bd*2"\nd2 $ s "hh*8"',  # no fence at all
        'Sure! A dub groove.\nd1 $ s "bd"\n',  # prose, then bare code
        'tidal> d1 $ s "cp"\n',
        '```\nd1 $ s "bd" -- kick\n```\n```haskell\nnot shown\n```',
    ]
    for reply in replies:
        expected = extract_code(reply.split("```\n```")[0] if reply.count("```") > 2 else reply)
        for sizes in ([1], [3, 7], [2, 11, 1, 5], [1000]):
            assert "\n".join(_stream(reply, sizes)).strip() == expected, (reply, sizes)


def test_code_stream_holds_back_partial_lines():
    from selene.blocks import CodeStream
    s = CodeStream()
    assert s.feed("```haskell\nd1 $ s \"80") == []
    assert s.feed("8bd*4\"\nd2") == ['d1 $ s "808bd*4"']
    assert s.finish() == ["d2"]


def test_describe_change():
    from selene.blocks import describe_change
    old = 'd2 $ n (scale "dorian" "0 2 <4 3> 7") # s "superpiano" # legato 1.2'
    assert describe_change(old, old.replace("<4 3>", "<4 5>")) == '"0 2 <4 3> 7" → "0 2 <4 5> 7"'
    assert describe_change(old, old + " # room 0.3") == "+ # room 0.3"
    assert describe_change(old, old.replace("d2 $ ", "d2 $ every 4 (fast 2) $ ")) == \
        "+ every 4 (fast 2) $"
    assert describe_change(old, old.replace("superpiano", "supervibe")) == \
        '"superpiano" → "supervibe"'
    assert describe_change(old, old) == "no change to the code"
