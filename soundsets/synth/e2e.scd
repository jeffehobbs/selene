// The Synth pack's end-to-end check: events for ../e2e.scd, which plays
// them in a silent SuperDirt. build.py's EXPECT["synth"] says what each
// recording must show. Each event: label, seconds from the start, and
// /dirt/play key-value pairs (cps 0.5, delta 0.5 and orbit 0 unless given).
[
    ["synbass note -24", 0.5, [\s, \synbass, \note, -24, \voice, 0]],
    ["synbass note -12", 1.5, [\s, \synbass, \note, -12, \voice, 0]],
    ["synpad delta 0.25", 2.5, [\s, \synpad, \delta, 0.25]],
    ["synpad delta 2", 3.5, [\s, \synpad, \delta, 2]],
    ["synpad delta 0.5 legato 3", 6.0, [\s, \synpad, \delta, 0.5, \legato, 3]],
    ["synpluck decay 2 on a 16th step", 8.0, [\s, \synpluck, \delta, 0.125, \decay, 2]],
    ["synpluck decay 2 fdecay 0.2", 10.5, [\s, \synpluck, \delta, 0.125, \decay, 2, \fdecay, 0.2]],
    ["synpluck decay 2 sustain 0.1 (choked)", 11.5, [\s, \synpluck, \decay, 2, \sustain, 0.1]],
    ["synbell decay 0.5", 12.0, [\s, \synbell, \decay, 0.5]],
    ["synbell decay 3", 13.0, [\s, \synbell, \decay, 3]],
    ["synacid voice 0", 16.5, [\s, \synacid, \note, -24, \voice, 0]],
    ["synacid voice 1", 17.25, [\s, \synacid, \note, -24, \voice, 1]],
    ["synacid voice 0 fvoice 1", 18.0, [\s, \synacid, \note, -24, \voice, 0, \fvoice, 1]],
    ["synlead slide 12", 19.0, [\s, \synlead, \slide, 12, \lfo, 0, \delta, 0.8]],
    ["synlead slide 0", 20.0, [\s, \synlead, \lfo, 0, \delta, 0.8]],
    ["synlpg voice 0", 21.0, [\s, \synlpg, \voice, 0]],
    ["synlpg voice 1", 22.0, [\s, \synlpg, \voice, 1]],
    ["syndrone delta 2", 23.0, [\s, \syndrone, \delta, 2]],
    ["synbass accelerate 1", 25.5, [\s, \synbass, \note, -24, \voice, 0, \accelerate, 1, \delta, 1]],
    ["moddrone:7 sample", 27.0, [\s, \moddrone, \n, 7]],
    ["modtex:3 sample", 33.5, [\s, \modtex, \n, 3]],
    ["modstab:5 sample", 41.0, [\s, \modstab, \n, 5]],
    ["modsweep:12 sample", 42.0, [\s, \modsweep, \n, 12]],
]
