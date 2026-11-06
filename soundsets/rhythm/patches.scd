// Selene Rhythm: modular-style drum patches.
//
// Each voice is a function that builds a mono UGen graph out of named
// controls, so the same code becomes a sample bank (soundsets/render.scd
// wraps it in Out.ar and renders it offline) and a live SuperDirt synth
// (soundsets/live.scd wraps it in DirtPan). Core UGens only: no sc3-plugins.
//
// Knobs every voice understands (Tidal params that start no SuperDirt effect):
//   voice  0–1, timbre (each voice says what it means)
//   decay  seconds
//   drive  0–1, tanh saturation
//   pitch1 voice-specific sweep or drop amount
//   n / note / octave  tune relative to the default note (C5, 261.63 Hz)
// Flow moves its own copies (fvoice +, fdecay ×, fdrive +), never the knobs
// the code set, so a written `# decay 0.8` stays authored.
//
// Loading this file returns (voices: (name: (...)), kit: [names]).
// A voice: fn (the graph; must free itself), gain (calibration), live (also
// a syn* synth), defaults (knob values the synth starts from), vary (t, i ->
// knob settings for bank variant i, t = i/15 along the bank's axis).
(
var voice = { |d = 0.5| (\voice.kr(d) + \fvoice.kr(0)).clip(0, 1) };
var decay = { |d = 0.5| (\decay.kr(d) * \fdecay.kr(1)).clip(0.005, 8) };
var drive = { |d = 0| (\drive.kr(d) + \fdrive.kr(0)).clip(0, 1) };
var pitch1 = { |d = 1| \pitch1.kr(d) };
var tune = { \freq.kr(261.6256) / 261.6256 };
var sat = { |sig, amt| var g = 1 + (amt * 9); (sig * g).tanh / g.tanh };
var free = { |secs| Line.kr(0, 0, secs, doneAction: 2) };
var hat = { |d|
    var dec = decay.(d), vo = voice.(0.5), dr = drive.(0), t = tune.();
    // The 808's six square oscillators, through a band-pass that voice moves.
    var metal = Mix(Pulse.ar([205.3, 304.4, 369.6, 522.7, 540, 800] * t, 0.5)) * 0.25;
    var sig = BPF.ar(metal, vo.linexp(0, 1, 5500, 11000), 0.5)
        + (HPF.ar(WhiteNoise.ar, 9000) * 0.15 * vo);
    var env = EnvGen.ar(Env([0, 1, 0.5, 0], [0.0003, dec * 0.2, dec * 0.8], [0, -3, -5]),
        doneAction: 2);
    sat.(HPF.ar(sig, 6000) * env, dr)
};
var voices = (
    kick: (
        live: true, gain: 0.44,
        defaults: (decay: 0.5, voice: 0.4, drive: 0.3, pitch1: 4),
        fn: {
            var dec = decay.(0.5), vo = voice.(0.4), dr = drive.(0.3), sweep = pitch1.(4);
            var base = 48 * tune.();
            // Sine VCO with an exponential pitch envelope (sweep = how far above base it starts).
            var f = base * (1 + (sweep * EnvGen.ar(Env.perc(0, 0.025 + (dec * 0.06), 1, -6))));
            var body = SinOsc.ar(f) + (SinOsc.ar(f * 2) * 0.08 * vo);
            var beater = SinOsc.ar(1600) * EnvGen.ar(Env.perc(0, 0.004)) * (0.1 + (vo * 0.4));
            var click = HPF.ar(WhiteNoise.ar, 3500) * EnvGen.ar(Env.perc(0, 0.003)) * vo * 0.5;
            var env = EnvGen.ar(Env([0, 1, 1, 0], [0.0008, dec * 0.15, dec * 0.85], [0, 0, -4]),
                doneAction: 2);
            sat.((body + beater + click) * env, dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.15, 1.3), tune: rrand(0.88, 1.12) * t.linlin(0, 1, 1.1, 0.9),
            pitch1: rrand(2.5, 7), voice: rrand(0.1, 0.8), drive: rrand(0.1, 0.6)) },
    ),
    snare: (
        live: true, gain: 0.81,
        defaults: (decay: 0.22, voice: 0.5, drive: 0.1),
        fn: {
            var dec = decay.(0.22), vo = voice.(0.5), dr = drive.(0.1), t = tune.();
            var f = 185 * t, bend = EnvGen.ar(Env.perc(0, 0.03)) * 0.4 + 1;
            // Shell: two tuned sines. Wires: band-passed and high noise. voice crossfades them.
            var shell = (SinOsc.ar(f * bend) + (SinOsc.ar(f * 1.52 * bend) * 0.5))
                * EnvGen.ar(Env.perc(0.0005, dec * 0.45, 1, -5));
            var noise = WhiteNoise.ar;
            var wires = ((BPF.ar(noise, 3200 * t.sqrt, 0.8) * 1.6) + (HPF.ar(noise, 6000) * 0.15))
                * EnvGen.ar(Env.perc(0.0005, dec, 1, -5));
            free.(dec + 0.05);
            sat.(HPF.ar((shell * (1 - (vo * 0.8))) + (wires * (0.2 + (vo * 0.8))), 100), dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.09, 0.5), voice: rrand(0.25, 0.8),
            tune: rrand(0.8, 1.3), drive: rrand(0, 0.4)) },
    ),
    hat: (
        live: true, gain: 7.6,
        defaults: (decay: 0.06, voice: 0.5),
        fn: { hat.(0.06) },
        vary: { |t| (decay: t.linexp(0, 1, 0.02, 0.16), voice: rrand(0.2, 0.9), tune: rrand(0.85, 1.25)) },
    ),
    ohat: (
        live: false, gain: 7.6,
        fn: { hat.(0.45) },
        vary: { |t| (decay: t.linexp(0, 1, 0.25, 1.1), voice: rrand(0.2, 0.9), tune: rrand(0.85, 1.25)) },
    ),
    clap: (
        live: true, gain: 0.88,
        defaults: (decay: 0.25, voice: 0.5, drive: 0.1),
        fn: {
            var dec = decay.(0.25), vo = voice.(0.5), dr = drive.(0.1), t = tune.();
            var gap = 0.008 + (vo * 0.008);  // voice spreads the hands apart
            // Three quick bursts, then a fourth that rings out into the tail.
            var bursts = EnvGen.ar(Env([0, 1, 0.1, 1, 0.1, 1, 0], [0.0004, gap, 0.0004, gap, 0.0004, gap],
                [0, -6, 0, -6, 0, -6]));
            var tail = EnvGen.ar(Env([0, 0, 1, 0], [gap * 3, 0.0004, dec], [0, 0, -5]));
            var noise = WhiteNoise.ar;
            var body = BPF.ar(noise, 1200 * t, 0.5) * 2;
            var air = BPF.ar(noise, 2500 * t, 0.8);
            free.((gap * 3) + dec + 0.05);
            sat.(HPF.ar((body * bursts) + (((body * 0.7) + (air * 0.5)) * tail), 300), dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.08, 0.7), voice: rrand(0.2, 0.9), tune: rrand(0.85, 1.2)) },
    ),
    tom: (
        live: true, gain: 0.5,
        defaults: (decay: 0.4, voice: 0.3, drive: 0.15, pitch1: 0.5),
        fn: {
            var dec = decay.(0.4), vo = voice.(0.3), dr = drive.(0.15), drop = pitch1.(0.5);
            var f = 110 * tune.();
            var bend = EnvGen.ar(Env([1 + (drop * 0.6), 1], [dec * 0.5], -3));
            var body = SinOsc.ar(f * bend) + (LFTri.ar(f * bend * 1.5) * 0.1 * vo);
            var thump = LPF.ar(WhiteNoise.ar, 1200) * EnvGen.ar(Env.perc(0, 0.015)) * (0.1 + (vo * 0.3));
            var env = EnvGen.ar(Env.perc(0.0008, dec, 1, -4), doneAction: 2);
            sat.((body + thump) * env, dr)
        },
        vary: { |t| (tune: t.linexp(0, 1, 0.6, 2.4), decay: (1 - t).linexp(0, 1, 0.25, 0.7),
            pitch1: rrand(0.2, 0.8), voice: rrand(0.1, 0.6)) },
    ),
    rim: (
        live: false, gain: 1,
        fn: {
            var dec = decay.(0.05), vo = voice.(0.5), dr = drive.(0.2), t = tune.();
            var exc = Impulse.ar(0) + (HPF.ar(WhiteNoise.ar, 2000) * EnvGen.ar(Env.perc(0, 0.002)) * 0.5);
            var sig = Ringz.ar(exc, [455, 1667, 2450] * t * vo.linlin(0, 1, 0.8, 1.25),
                [dec, dec * 0.7, dec * 0.4], [0.5, 0.3, 0.15]).sum;
            free.((dec * 1.5) + 0.05);
            sat.(HPF.ar(sig, 300), dr)
        },
        vary: { |t| (tune: t.linexp(0, 1, 0.75, 1.4), decay: rrand(0.03, 0.08), voice: rrand(0.3, 0.7)) },
    ),
    cow: (
        live: false, gain: 1,
        fn: {
            var dec = decay.(0.3), vo = voice.(0.5), dr = drive.(0.1), t = tune.();
            var osc = Pulse.ar([540, 800] * t, 0.5).sum * 0.5;
            var sig = BPF.ar(osc, vo.linexp(0, 1, 900, 2600), 0.6) + (LPF.ar(osc, 3000) * 0.3);
            var env = EnvGen.ar(Env([0, 1, 0.3, 0], [0.0005, 0.02, dec], [0, -4, -4]), doneAction: 2);
            sat.(sig * env, dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.08, 0.6), tune: rrand(0.85, 1.15), voice: rrand(0.3, 0.8)) },
    ),
    metal: (
        live: true, gain: 0.2,
        defaults: (decay: 0.6, voice: 0.5, drive: 0.1),
        fn: {
            var dec = decay.(0.6), vo = voice.(0.5), dr = drive.(0.1);
            var f = 320 * tune.();
            // FM pair, ring-modulated, then struck into an inharmonic resonator bank.
            var mod = SinOsc.ar(f * vo.linlin(0, 1, 1.17, 3.73)) * f * 2.5 * EnvGen.ar(Env.perc(0, dec * 0.4));
            var car = SinOsc.ar(f + mod);
            var ring = car * SinOsc.ar(f * 2.76);
            var res = Ringz.ar((car + ring) * EnvGen.ar(Env.perc(0, 0.01)), f * [1, 2.41, 3.83, 5.17],
                dec * [1, 0.7, 0.5, 0.3]).sum * 0.1;
            var sig = (((car * 0.5) + (ring * 0.5)) * EnvGen.ar(Env.perc(0.0005, dec, 1, -5))) + res;
            free.((dec * 1.2) + 0.05);
            sat.(HPF.ar(sig, 150), dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.08, 1.6), voice: 1.0.rand, tune: rrand(0.6, 1.8)) },
    ),
    zap: (
        live: true, gain: 0.36,
        defaults: (decay: 0.25, voice: 0.3, drive: 0.2, pitch1: 5),
        fn: {
            var dec = decay.(0.25), vo = voice.(0.3), dr = drive.(0.2), depth = pitch1.(5), t = tune.();
            // A sine diving from depth octaves above 80 Hz; voice adds FM grit.
            var f = EnvGen.ar(Env([80 * t * (2 ** depth.clip(0, 7)), 80 * t], [dec * 0.8], \exp));
            var sig = SinOsc.ar(f + (SinOsc.ar(f * 1.5) * f * vo));
            var env = EnvGen.ar(Env.perc(0.0005, dec, 1, -4), doneAction: 2);
            sat.(sig * env, dr)
        },
        vary: { |t| (decay: t.linexp(0, 1, 0.06, 0.7), pitch1: rrand(3, 6.5), voice: rrand(0, 0.6),
            tune: rrand(0.7, 1.4)) },
    ),
    perc: (
        live: false, gain: 1,
        fn: {
            var dec = decay.(0.15), vo = voice.(0.5), dr = drive.(0), f = 400 * tune.();
            // voice: 0 a skin (bongo-ish sine with a pitch drop) … 1 a struck wood resonator.
            var skin = SinOsc.ar(f * EnvGen.ar(Env([1.25, 1], [0.03], -4)))
                * EnvGen.ar(Env.perc(0.0005, dec, 1, -6));
            var exc = Impulse.ar(0) + (WhiteNoise.ar * EnvGen.ar(Env.perc(0, 0.0015)) * 0.5);
            var wood = Ringz.ar(exc, f * 1.4 * [1, 2.57, 4.1], dec * [0.5, 0.25, 0.12], [0.5, 0.25, 0.1]).sum;
            free.(dec + 0.05);
            sat.((skin * (1 - vo)) + (wood * vo), dr)
        },
        vary: { |t, i| (tune: t.linexp(0, 1, 0.45, 3), decay: rrand(0.08, 0.3),
            voice: if(i.even) { rrand(0, 0.3) } { rrand(0.7, 1) }) },
    ),
    noise: (
        live: false, gain: 1,
        fn: {
            var dec = decay.(0.5), vo = voice.(0.5), kind = \kind.kr(0);
            // kind 0 a snap, 1 a fall, 2 a rise.
            var cf = Select.ar(kind, [
                DC.ar(1) * vo.linexp(0, 1, 800, 6000),
                EnvGen.ar(Env([9000, 300], [dec], \exp)),
                EnvGen.ar(Env([300, 9000], [dec], \exp)),
            ]);
            var amp = Select.ar(kind, [
                EnvGen.ar(Env.perc(0.0005, dec, 1, -6)),
                EnvGen.ar(Env.perc(0.005, dec, 1, -2)),
                EnvGen.ar(Env([0, 1, 0], [dec * 0.95, dec * 0.05], [3, -4])),
            ]);
            free.(dec + 0.05);
            BPF.ar(WhiteNoise.ar, cf, 0.3 + (vo * 0.5)) * amp
        },
        vary: { |t, i| case
            { i < 6 } { (kind: 0, decay: (i / 5).linexp(0, 1, 0.03, 0.2), voice: rrand(0.2, 0.9)) }
            { i < 11 } { (kind: 1, decay: ((i - 6) / 4).linexp(0, 1, 0.3, 1.0), voice: rrand(0.2, 0.8)) }
            { (kind: 2, decay: ((i - 11) / 4).linexp(0, 1, 0.5, 2.0), voice: rrand(0.2, 0.8)) } },
    ),
);
// synkit:N plays these live synths; ohat is synhat with a long decay.
(voices: voices, kit: [\kick, \snare, \hat, [\hat, (decay: 0.45)], \clap, \tom, \metal, \zap])
)
