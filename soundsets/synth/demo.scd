// A listening demo of the Synth pack, played through a real SuperDirt by
// ../e2e.scd (build.py --check records it as reel/demo.wav). The drums are
// the Rhythm pack's synkit when that pack is built too; no claps.
// 120 bpm, cps 0.5: a bar is 2 s, a 16th 0.125 s. C minor throughout.
(
var ev = List.new, bar = 2.0, s16 = 0.125;
var hit = { |at, pairs| ev.add([pairs[1].asString, at, pairs]) };
// steps: a 16-step string; x hit, o soft, . rest.
var steps = { |b, str, pairs, gain = 1|
    str.do { |c, i|
        if(c == $x or: { c == $o }) {
            hit.((b * bar) + (i * s16), pairs ++ [\gain, gain * if(c == $o) { 0.8 } { 1 }])
        };
    };
};
var drums = { |b, kick = true, hats = true, snare = true, ohat = false|
    if(kick) { steps.(b, "x...x...x...x...", [\s, \synkit, \n, 0]) };
    if(hats) { steps.(b, "..x...x...x...x.", [\s, \synkit, \n, 2], 0.85) };
    if(ohat) { steps.(b, "..x...x...x...x.", [\s, \synkit, \n, 3, \decay, 0.18], 0.8) };
    if(snare) { steps.(b, "....x.......x...", [\s, \synkit, \n, 1], 0.9) };
};
// The acid line: 16th-step notes (nil rests), slides on the ones after a jump.
var acid = [0, nil, 12, 0, nil, 3, 0, 15, nil, 0, 7, nil, 10, 12, nil, 3];
var acidBar = { |b, vo, dec|
    acid.do { |n, i|
        n !? {
            hit.((b * bar) + (i * s16), [\s, \synacid, \note, n - 24, \delta, s16, \legato, 0.7,
                \voice, vo, \decay, dec, \slide, if(i == 13 or: { i == 7 }) { 2 } { 0 }, \gain, 0.95])
        };
    };
};
// Chords for the middle: root (for the bass) and pad voicing.
var chords = [
    [-24, [-12, -9, -5, -2, 2]],    // Cm9
    [-28, [-16, -12, -9, -5]],      // Abmaj7
    [-31, [-19, -16, -12, -9, -5]], // Fm9
    [-29, [-17, -12, -10, -7]],     // G7sus4
];
var bassBar = { |b, root|
    "x..x..x.x..x..x.".do { |c, i|
        if(c == $x) {
            hit.((b * bar) + (i * s16), [\s, \synbass, \note, root + if(i == 8 or: { i == 14 }) { 12 } { 0 },
                \delta, s16 * 2, \legato, 0.8, \voice, 0.35, \decay, 0.25, \drive, 0.25])
        };
    };
};
var padBar = { |b, notes|
    notes.do { |n| hit.(b * bar, [\s, \synpad, \note, n, \delta, bar, \legato, 1, \voice, 0.45,
        \decay, 0.6, \gain, 0.85, \room, 0.3, \size, 0.7]) };
};
var pluckBar = { |b, notes|
    var up = (notes + 24).select { |n| n < 22 };
    16.do { |i|
        var tones = up ++ up.reverse.drop(1).drop(-1);
        hit.((b * bar) + (i * s16), [\s, \synpluck, \note, tones.wrapAt(i), \decay, 0.9, \voice, 0.65,
            \pan, (i / 15).linlin(0, 1, 0.2, 0.8), \gain, 0.7, \room, 0.35, \size, 0.6])
    };
};
var t;

// A (bars 0-3): the acid line opening up over kick and open hats.
4.do { |b|
    drums.(b, true, false, b > 1, true);
    acidBar.(b, b.linlin(0, 3, 0.25, 0.9), b.linlin(0, 3, 0.15, 0.45));
};
// B (bars 4-7): bass, pad chords, a plucked arpeggio, the full kit.
4.do { |k|
    var b = 4 + k, c = chords[k];
    drums.(b);
    bassBar.(b, c[0]);
    padBar.(b, c[1]);
    pluckBar.(b, c[1]);
};
// C (bars 8-11): no drums. A drone, a bell melody, the West-coast voice, textures.
hit.(8 * bar, [\s, \syndrone, \note, -12, \delta, 4 * bar, \voice, 0.35, \decay, 2, \gain, 0.9]);
hit.(8 * bar, [\s, \modtex, \n, 6, \gain, 0.9]);
hit.(10 * bar, [\s, \modtex, \n, 11, \gain, 0.8]);
[[0, 12], [3, 15], [6, 19], [8, 17], [12, 22], [16, 19], [19, 15], [22, 24], [24, 22], [28, 19]].do { |pair|
    hit.((8 * bar) + (pair[0] * 2 * s16), [\s, \synbell, \note, pair[1], \decay, 2.5, \voice, 0.47,
        \gain, 0.8, \room, 0.5, \size, 0.85, \pan, 0.4])
};
64.do { |i|
    var notes = [0, 7, 12, 3, 10, 7, 15, 12];
    if(#[0, 3, 5, 6, 8, 11, 13, 14].includes(i % 16)) {
        hit.((8 * bar) + (i * s16), [\s, \synlpg, \note, notes.wrapAt(i * 3) - 12, \decay, 0.3,
            \voice, (i / 63 * 2pi).sin.linlin(-1, 1, 0.35, 0.9), \pan, [0.3, 0.7].wrapAt(i), \gain, 1,
            \room, 0.25])
    };
};
// D (bars 12-15): drums back, bass, a lead melody with slides and vibrato, dub stabs.
4.do { |k|
    var b = 12 + k;
    drums.(b, true, true, true, k.odd);
    bassBar.(b, -24);
    [6, 14].do { |i| hit.((b * bar) + (i * s16), [\s, \modstab, \n, 2, \note, [0, 0, -4, -2].at(k),
        \gain, 0.75, \delay, 0.45, \delaytime, 0.375, \delayfeedback, 0.55, \orbit, 1]) };
};
[
    // 16th step from bar 12, note, length in 16ths, slide
    [0, 7, 3, 0], [3, 10, 3, 0], [6, 12, 6, 2], [12, 15, 4, 0], [16, 14, 2, 0], [18, 12, 2, 0],
    [20, 10, 4, 0], [24, 7, 8, 0], [32, 12, 3, 5], [35, 15, 3, 0], [38, 19, 6, 2], [44, 17, 4, 0],
    [48, 15, 4, 0], [52, 14, 4, 0], [56, 12, 8, 0],
].do { |n|
    hit.((12 * bar) + (n[0] * s16), [\s, \synlead, \note, n[1], \delta, n[2] * s16, \legato, 0.95,
        \slide, n[3], \lfo, 0.3, \voice, 0.5, \gain, 0.85, \room, 0.25])
};
// E (bars 16-17): the acid returns, squelchy, under a riser into bar 18.
2.do { |k| var b = 16 + k; drums.(b, true, true, false, true); acidBar.(b, 0.95, 0.5) };
t = 4 ** (5 / 7);  // modsweep:13's length (patches.scd: risers run 1-4 s)
hit.((18 * bar) - t, [\s, \modsweep, \n, 13, \gain, 0.8]);
// F (bars 18-21): the drop lands on a stab and a fall, then a drone and a glassy texture.
hit.(18 * bar, [\s, \modstab, \n, 6, \gain, 0.9, \room, 0.4, \size, 0.8]);
hit.(18 * bar, [\s, \synkit, \n, 0]);
hit.(18 * bar, [\s, \modsweep, \n, 3, \gain, 0.7]);
hit.(18 * bar + 0.5, [\s, \moddrone, \n, 0, \gain, 0.9]);
hit.(18 * bar + 0.5, [\s, \modtex, \n, 14, \gain, 0.8]);
[[1, 24], [4, 19], [7, 22], [10, 15], [14, 12]].do { |pair|
    hit.((18 * bar) + (pair[0] * 2 * s16), [\s, \synpluck, \note, pair[1], \decay, 2.5, \voice, 0.4,
        \gain, 0.75, \room, 0.5, \size, 0.85])
};
(events: ev.asArray.sort { |a, b| a[1] <= b[1] }, tail: 7)
)
