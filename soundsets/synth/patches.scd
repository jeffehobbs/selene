// Selene Synth: modular-style melodic patches.
//
// The same contract as rhythm/patches.scd: each voice builds a UGen graph out
// of named controls, so render.scd turns it into a sample bank and live.scd
// into a SuperDirt synth. Core UGens only: no sc3-plugins.
//
// Knobs (Tidal params that start no SuperDirt effect; each voice says what
// it does with them):
//   voice  0–1, timbre      decay  seconds      drive  0–1, tanh saturation
//   slide  semitones the note glides up into it from (negative: down into it)
//   lfo    vibrato or filter movement            detune  0–1, oscillator spread
//   n / note / octave       pitch (note 0 = C5, 261.63 Hz, like every Tidal synth)
//   accelerate              pitch bend over the note, as on SuperDirt's synths
// Flow moves its own copies (fvoice +, fdecay ×, fdrive +), never the knobs
// the code set.
//
// Two kinds of live voice. `gated: true` voices (bass, acid, lead, pad,
// drone) last the event's sustain, so Tidal's step length, legato and
// sustain shape the note the way they do on supersaw. The rest (pluck,
// bell, lpg) are struck: they ring for their decay whatever the step, like a
// sample, and `# sustain` chokes them.
//
// Voices without `vary` are live only; voices without `live` are banks only.
// `channels: 2` voices render stereo banks; live, DirtPan balances them.
(
var voice = { |d = 0.5| (\voice.kr(d) + \fvoice.kr(0)).clip(0, 1) };
var decay = { |d = 0.5| (\decay.kr(d) * \fdecay.kr(1)).clip(0.005, 12) };
var drive = { |d = 0| (\drive.kr(d) + \fdrive.kr(0)).clip(0, 1) };
var sustain = { \sustain.kr(1).max(0.01) };
var tune = { \freq.kr(261.6256) / 261.6256 };
// The note's frequency, bent by accelerate over secs.
var pitch = { |secs| \freq.kr(261.6256) * Line.kr(1, 1 + \accelerate.kr(0), secs) };
// slide: start that many semitones below the note and glide up into it.
var glide = { |f, time = 0.08|
    f * (2 ** (\slide.kr(0).neg / 12 * EnvGen.kr(Env([1, 0], [time], -3))))
};
var sat = { |sig, amt| var g = 1 + (amt * 9); (sig * g).tanh / g.tanh };
var free = { |secs| Line.kr(0, 0, secs, doneAction: 2) };
// A gate over the note's sustain: attack, hold, release, all inside sus
// (SuperDirt frees the synth when sustain ends, so a tail can't spill over).
var gate = { |sus, atk = 0.005, rel = 0.05, curve = -4|
    var a = atk.min(sus * 0.4), r = rel.min(sus * 0.5);
    EnvGen.ar(Env([0, 1, 1, 0], [a, (sus - a - r).max(0), r], [2, 0, curve]), doneAction: 2)
};
var chords = [
    [0, 3, 7], [0, 3, 7, 10], [0, 3, 7, 10, 14], [0, 3, 10, 14, 17],  // minor: m m7 m9 m11
    [0, 4, 7], [0, 4, 7, 11], [0, 4, 7, 11, 14], [0, 4, 7, 9, 14],    // major: maj maj7 maj9 6/9
    [0, 2, 7], [0, 5, 7], [0, 5, 7, 10], [0, 5, 10, 15],              // open: sus2 sus4 7sus4 quartal
    [0, 4, 7, 10], [0, 4, 7, 10, 14], [0, 3, 6, 9], [0, 4, 8],        // tense: 7 9 dim7 aug
];
var voices = (
    bass: (
        live: true, gated: true, gain: 0.41,
        defaults: (sustain: 0.5, tune: 0.25, voice: 0.4, decay: 0.3, drive: 0.2),
        fn: {
            var sus = sustain.(), vo = voice.(0.4), dec = decay.(0.3), dr = drive.(0.2);
            var f = glide.(pitch.(sus), 0.05);
            // A sub sine plus a saw whose low-pass opens with voice and the envelope.
            var fenv = EnvGen.ar(Env.perc(0.004, dec, 1, -4));
            var cut = (f * vo.linexp(0, 1, 1.5, 8) * (1 + (fenv * vo * 4))).clip(30, 14000);
            var saw = RLPF.ar(Saw.ar(f), cut, 0.7);
            var sig = (SinOsc.ar(f) * (1 - (vo * 0.5))) + (saw * vo.linlin(0, 1, 0.1, 0.8));
            sat.(HPF.ar(sig, 25) * gate.(sus, 0.003, 0.03), dr)
        },
    ),
    acid: (
        live: true, gated: true, gain: 0.49,
        defaults: (sustain: 0.2, tune: 0.25, voice: 0.6, decay: 0.3, drive: 0.3),
        fn: {
            var sus = sustain.(), vo = voice.(0.6), dec = decay.(0.3), dr = drive.(0.3);
            var f = glide.(pitch.(sus), 0.06);
            // Saw into a resonant 4-pole ladder; voice is the envelope amount (the accent).
            var fenv = EnvGen.ar(Env.perc(0.003, dec, 1, -4));
            var cut = ((f * 1.2) + 120 + (fenv * vo.linexp(0, 1, 200, 7000))).clip(30, 18000);
            var sig = MoogFF.ar(Saw.ar(f), cut, 3.3) * 2.5;
            sat.(HPF.ar(sig, 30) * gate.(sus, 0.002, 0.02), dr)
        },
    ),
    lead: (
        live: true, gated: true, gain: 0.31,
        defaults: (sustain: 0.6, voice: 0.5, decay: 0.4, drive: 0.15, lfo: 0.2, detune: 0.3),
        fn: {
            var sus = sustain.(), vo = voice.(0.5), dec = decay.(0.4), dr = drive.(0.15);
            var det = \detune.kr(0.3).clip(0, 1);
            // lfo: vibrato depth in semitones, fading in after the attack.
            var vib = SinOsc.kr(5.5, 0, \lfo.kr(0.2) * EnvGen.kr(Env([0, 0, 1], [0.12, 0.3])));
            var f = glide.(pitch.(sus), 0.12) * (2 ** (vib / 12));
            // Two pulse waves: voice is how far the width swings (PWM), detune spreads the pair.
            var width = (0.5 + SinOsc.kr(1.3, 0, vo * 0.4)).clip(0.05, 0.95);
            var osc = Pulse.ar(f, width) + (Pulse.ar(f * (1 + (det * 0.012)), 1 - width) * 0.6);
            var fenv = EnvGen.ar(Env.perc(0.01, dec, 1, -3));
            var sig = RLPF.ar(osc, ((f * 3) + 600 + (fenv * vo.linexp(0, 1, 1500, 6000))).clip(50, 16000), 0.5);
            sat.(sig * 0.5 * gate.(sus, 0.008, 0.06), dr)
        },
    ),
    pad: (
        live: true, gated: true, gain: 0.91, channels: 2,
        defaults: (sustain: 2, tune: 0.5, voice: 0.4, decay: 0.5, detune: 0.5, lfo: 0.5),
        fn: {
            var sus = sustain.(), vo = voice.(0.4), dec = decay.(0.5), dr = drive.(0);
            var det = \detune.kr(0.5).clip(0, 1), f = pitch.(sus);
            // Six detuned saws, odd ones left and even ones right, under a slow low-pass.
            var spread = [-1, -0.6, -0.2, 0.2, 0.6, 1] * det * 0.3;
            var saws = spread.collect { |s| Saw.ar(f * (2 ** (s / 12))) };
            var pair = [saws.at([0, 2, 4]).sum, saws.at([1, 3, 5]).sum] / 3;
            var move = SinOsc.kr([0.13, 0.17], [0, 1], \lfo.kr(0.5).clip(0, 1) * 0.6, 1);
            var cut = (f * vo.linexp(0, 1, 1.2, 14) * move).clip(60, 16000);
            // decay is the swell: attack time, with a longer release.
            var env = gate.(sus, dec, dec * 1.5, -3);
            sat.(RLPF.ar(pair, cut, 0.6) * env, dr)
        },
    ),
    drone: (
        live: true, gated: true, gain: 0.79, channels: 2, count: 12,
        defaults: (sustain: 4, tune: 0.5, voice: 0.4, decay: 1.5, detune: 0.3, lfo: 0.5),
        fn: {
            var sus = sustain.(), vo = voice.(0.4), dec = decay.(1.5), dr = drive.(0);
            var det = \detune.kr(0.3).clip(0, 1), f = pitch.(sus);
            // Three oscillators beating slowly: the note, a detuned copy, and an octave below.
            var osc = { |fr| (SinOsc.ar(fr) * (1 - vo)) + (Saw.ar(fr) * vo * 0.6) };
            var a = osc.(f), b = osc.(f * (1 + (det * 0.008))), c = osc.(f * 0.5 * (1 - (det * 0.004)));
            var pair = [a + (c * 0.7), b + (c * 0.7)] * 0.45;
            var move = SinOsc.kr([0.07, 0.05], [0, 2], \lfo.kr(0.5).clip(0, 1) * 0.5, 1);
            var cut = (f * vo.linexp(0, 1, 2, 16) * move).clip(60, 16000);
            sat.(LPF.ar(pair, cut) * gate.(sus, dec, dec * 1.5, -3), dr)
        },
        // moddrone:N is a drone on pitch class N (0 C … 11 B), an octave below C5.
        vary: { |t, i| (tune: 0.5 * (2 ** (i / 12)), sustain: 6, decay: 1.6, voice: rrand(0.15, 0.6),
            detune: rrand(0.2, 0.7), lfo: rrand(0.3, 0.9)) },
    ),
    pluck: (
        live: true, gain: 0.98,
        defaults: (decay: 1.5, voice: 0.5),
        fn: {
            var dec = decay.(1.5), vo = voice.(0.5), dr = drive.(0), f = pitch.(dec);
            // Karplus-Strong: a noise burst into a tuned, damped delay line.
            var exc = LPF.ar(WhiteNoise.ar, vo.linexp(0, 1, 1200, 16000));
            var sig = Pluck.ar(exc, Impulse.ar(0), 0.05, f.reciprocal, dec, vo.linlin(0, 1, 0.65, 0.1));
            free.(dec + 0.1);
            sat.(LeakDC.ar(sig), dr)
        },
    ),
    bell: (
        live: true, gain: 0.35,
        defaults: (decay: 2.5, voice: 0.47),
        fn: {
            var dec = decay.(2.5), vo = voice.(0.47), dr = drive.(0), f = pitch.(dec);
            // Two-operator FM; voice sets the modulator ratio (most settings inharmonic),
            // and the index falls faster than the tone, so it starts bright and mellows.
            var ratio = vo.linlin(0, 1, 1, 4);
            var index = EnvGen.ar(Env.perc(0.001, dec * 0.4, 1, -4)) * 2.5 + 0.3;
            var car = SinOsc.ar(f + (SinOsc.ar(f * ratio) * f * ratio * index));
            var shimmer = SinOsc.ar(f * 2.756) * EnvGen.ar(Env.perc(0.001, dec * 0.3, 1, -4)) * 0.15;
            var env = EnvGen.ar(Env.perc(0.002, dec, 1, -4), doneAction: 2);
            sat.((car + shimmer) * env * 0.7, dr)
        },
    ),
    lpg: (
        live: true, gain: 0.3,
        defaults: (decay: 0.6, voice: 0.4),
        fn: {
            var dec = decay.(0.6), vo = voice.(0.4), dr = drive.(0), f = pitch.(dec);
            // West-coast: a sine folds back on itself harder as voice rises, then a
            // low-pass gate (filter and level on one slow, vactrol-like envelope).
            var env = EnvGen.ar(Env.perc(0.001, dec, 1, -4));
            var vac = LagUD.ar(env, 0.002, 0.03);
            var sine = SinOsc.ar(f + (SinOsc.ar(f * 2) * f * vo * 0.5 * vac));
            var folded = (sine * (1 + (vo * 6 * vac)) * 0.5pi).sin;
            var sig = LPF.ar(folded, vac.linexp(0, 1, 80, 12000)) * vac;
            free.(dec + 0.15);
            sat.(sig, dr)
        },
    ),
    tex: (
        gain: 1, channels: 2,
        fn: {
            var sus = sustain.(), vo = voice.(0.5), f = 130.8128 * tune.(), move = \lfo.kr(0.5);
            // A cloud of near-harmonic partials swelling in and out; voice lifts it
            // toward the upper partials, glassy resonator glints and airy wind.
            var ratios = [1, 2, 3, 4.02, 5.04, 6, 7.1, 8.07, 9.2, 12.1, 14.3, 16.2];
            var cloud = ratios.collect { |r, k|
                var w = (k / (ratios.size - 1)), lean = (1 - (w - vo).abs).max(0) ** 2;
                Pan2.ar(SinOsc.ar(f * r * LFNoise2.kr(0.1, 0.004, 1)),
                    LFNoise1.kr(0.1 + (k * 0.01)),
                    LFNoise2.kr(0.12 + (k * 0.03) * (0.5 + move)).range(0, 1).squared * lean / r.sqrt)
            }.sum;
            var glint = Klank.ar(`[f * [4, 6, 8.07, 12.1, 16.2], [0.5, 0.4, 0.3, 0.25, 0.2], [0.8, 0.6, 0.5, 0.4, 0.3]],
                Dust.ar([2, 2] * (0.5 + (vo * 3)), 0.04 * vo));
            var wind = BPF.ar(PinkNoise.ar([0.3, 0.3]),
                LFNoise2.kr([0.13, 0.11] * (0.5 + move)).exprange(f * 2, f * vo.linexp(0, 1, 4, 60)), 0.15);
            var env = EnvGen.ar(Env([0, 1, 1, 0], [sus * 0.35, sus * 0.3, sus * 0.35], \sin), doneAction: 2);
            ((cloud * 0.6) + glint + (wind * (0.2 + (vo * 0.5)))) * env
        },
        vary: { |t| (voice: t.linlin(0, 1, 0.05, 0.95), tune: 2 ** ((t * 2.99).floor - 1),
            sustain: rrand(5, 7), lfo: rrand(0.2, 1)) },
    ),
    stab: (
        gain: 1, channels: 2,
        fn: {
            var dec = decay.(0.4), vo = voice.(0.5), dr = drive.(0.1), f = 130.8128 * tune.();
            // A chord of detuned saw pairs, left and right, through a low-pass that
            // snaps shut: the dub stab. \c0-\c4 are its intervals (-1: unused).
            var notes = [\c0.kr(0), \c1.kr(4), \c2.kr(7), \c3.kr(-1), \c4.kr(-1)];
            var pair = notes.collect { |iv|
                var fr = f * (2 ** (iv.max(0) / 12)), on = iv >= 0;
                [Saw.ar(fr * 0.997), Saw.ar(fr * 1.003)] * on
            }.sum / 3;
            var fenv = EnvGen.ar(Env.perc(0.002, dec * 0.5, 1, -4));
            var cut = (f * (2 + (fenv * vo.linexp(0, 1, 6, 40)))).clip(60, 16000);
            var env = EnvGen.ar(Env.perc(0.003, dec, 1, -4), doneAction: 2);
            sat.(RLPF.ar(pair, cut, 0.5) * env, dr)
        },
        vary: { |t, i| var c = chords[i] ++ [-1, -1];
            (c0: c[0], c1: c[1], c2: c[2], c3: c[3], c4: c[4], decay: rrand(0.3, 0.6), voice: rrand(0.3, 0.7),
                drive: rrand(0.05, 0.3)) },
    ),
    sweep: (
        gain: 1, channels: 2,
        fn: {
            var sus = sustain.(), vo = voice.(0.5), rise = \kind.kr(0), f = 130.8128 * tune.();
            // kind 0 falls, 1 rises. A detuned saw stack and noise under a
            // resonant low-pass sweeping the whole length; risers also climb in
            // pitch and stop dead at the top, so they land on the next downbeat.
            var pos = Select.ar(rise, [EnvGen.ar(Env([1, 0], [sus], -3)), EnvGen.ar(Env([0, 1], [sus], 3))]);
            var fr = f * (1 + (rise * pos * vo));
            var src = [Saw.ar(fr * [1, 2.004]).sum, Saw.ar(fr * [1.006, 1.998]).sum] * 0.4
                + (WhiteNoise.ar([0.2, 0.2]) * (0.3 + vo));
            var sig = RLPF.ar(src, pos.linexp(0, 1, 150, 12000), 0.2);
            var amp = Select.ar(rise, [
                EnvGen.ar(Env([0, 1, 0], [0.005, sus], [0, -2])),
                EnvGen.ar(Env([0, 1, 0], [sus, 0.005], [3, 0])),
            ]);
            free.(sus + 0.02);
            (sig * amp).tanh
        },
        vary: { |t, i| if(i < 8) {
            (kind: 0, sustain: (i / 7).linexp(0, 1, 0.6, 2.5), voice: rrand(0.2, 0.8), tune: rrand(0.9, 1.1))
        } {
            (kind: 1, sustain: ((i - 8) / 7).linexp(0, 1, 1, 4), voice: rrand(0.4, 1), tune: rrand(0.9, 1.1))
        } },
    ),
);
(voices: voices)
)
