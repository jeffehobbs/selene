// Render a soundset's patches offline (NRT) into raw float WAVs.
// usage: sclang render.scd PATCHES.scd OUT_DIR [VARIANTS]
// Run it with an isolated HOME (build.py does) so the player's startup.scd,
// which boots a server, never runs.
//
// Writes OUT_DIR/mod<name>/mod<name>_NN.wav for every voice with a vary
// (count of them, 16 unless the voice says; stereo for channels: 2) and, for live voices,
// OUT_DIR/_live/<name>.wav at the synth's default knobs (for calibration).
// Prints one "RENDERED path key=value ..." line per file.
(
var args = thisProcess.argv;
var patches = args[0].load, out = args[1], count = (args[2] ? "16").asInteger;
var cond = Condition.new, t0 = Main.elapsedTime, done = 0;
var optsFor = { |channels| ServerOptions.new.numOutputBusChannels_(channels).numInputBusChannels_(0)
    .sampleRate_(48000).memSize_(8192 * 8) };
var seedFor = { |name, i| (name.asString.ascii.sum * 1009) + (i * 7919) };
var render = { |def, path, params, dur, seed, channels = 1|
    var score = Score([
        [0, ['/d_recv', def.asBytes]],
        [0, ['/s_new', def.name, 1000, 0, 0, \seed, seed] ++ params],
        [dur, ['/c_set', 0, 0]],
    ]);
    score.recordNRT(nil, path, nil, 48000, "wav", "float", optsFor.(channels), "", dur, { cond.unhang });
    cond.hang;
    ("RENDERED " ++ path ++ " " ++ params.clump(2).collect { |kv| "%=%".format(kv[0], kv[1].round(0.0001)) }.join(" ")).postln;
    done = done + 1;
};
// Knob settings -> synth arguments: tune is a ratio on the default note.
var toArgs = { |p|
    var args = p.copy;
    args[\tune] !? { |r| args[\freq] = 261.6256 * r; args.removeAt(\tune) };
    args.asPairs
};
// A gated note lasts its sustain; a struck one rings for its decay.
var lengthOf = { |p| p[\sustain] !? { |sus| sus + 0.05 } ?? { ((p[\decay] ? 0.5) * 1.6) + 0.15 } };

// Build every graph first: an error here would otherwise leave sclang idle
// forever (a script that fails doesn't exit).
var defs = try {
    patches[\voices].collect { |v, name|
        SynthDef(("nrt_" ++ name).asSymbol, {
            RandSeed.ir(1, \seed.ir(0));  // noise UGens repeat from build to build
            Out.ar(0, v[\fn].value * v[\gain]);
        })
    }
} { |e| e.reportError; "RENDER FAILED building the SynthDefs".postln; 1.exit };

fork {
    patches[\voices].keysValuesDo { |name, v|
        var def = defs[name];
        var dir = out +/+ "mod" ++ name, n = v[\count] ? count, ch = v[\channels] ? 1;
        // A voice without vary is live only: no bank.
        v[\vary] !? {
            File.mkdir(dir);
            n.do { |i|
                var p;
                thisThread.randSeed = seedFor.(name, i);
                p = v[\vary].value(i / (n - 1), i);
                render.(def, dir +/+ "mod%_%.wav".format(name, i.asString.padLeft(2, "0")),
                    toArgs.(p), lengthOf.(p), seedFor.(name, i), ch);
            };
        };
        if(v[\live] == true) {
            File.mkdir(out +/+ "_live");
            render.(def, out +/+ "_live" +/+ "syn" ++ name ++ ".wav",
                toArgs.(v[\defaults]), lengthOf.(v[\defaults]), seedFor.(name, 99), ch);
        };
    };
    "RENDER DONE % files in % s".format(done, (Main.elapsedTime - t0).round(0.1)).postln;
    0.exit;
};
)
