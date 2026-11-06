// A soundset's live synths, for a running SuperDirt. Ships in each pack as
// synths.scd next to patches.scd; Selene loads it with "…/synths.scd".load
// (again on every boot). Reloading replaces the synths in place.
//
// Each live voice becomes syn<name>: the patch's graph, panned by DirtPan.
// SuperDirt sends every Tidal param whose name matches a control (n/note ->
// freq, voice, decay, drive, pitch1, Flow's fvoice/fdecay/fdrive, pan).
// The kit (synkit) is one name whose n picks the drum, like a sample bank.
(
var dirt = ~dirt ? SuperDirt.default;
var dir = thisProcess.nowExecutingPath.dirname;
var patches = (dir +/+ "patches.scd").load;
var voices = patches[\voices];
// SuperDirt frees a synth after the event's sustain, which for a synth is
// the step length unless the pattern says otherwise, so a long kick on 16ths
// would be cut off. Like a sample's buffer length, unitDuration tells it how
// long the drum really rings (decay × Flow's fdecay). `# sustain`/`# legato`
// still choke it on purpose.
var ringsFor = { |v|
    var d = (v[\defaults] ? ())[\decay] ? 0.5;
    { (((~decay ? d) * (~fdecay ? 1)).clip(0.005, 8) * 1.2) + 0.1 }
};
var eventFor = { |name, extra|
    var v = voices[name], synth = ("syn" ++ name).asSymbol;
    (instrument: synth, unitDuration: ringsFor.(v)).putAll(extra ? ())
};
if(dirt.isNil) { "selene soundset: no SuperDirt running (~dirt is nil)".warn } {
    voices.keysValuesDo { |name, v|
        if(v[\live]) {
            var synth = ("syn" ++ name).asSymbol;
            SynthDef(synth, {
                var sig = v[\fn].value * v[\gain];
                OffsetOut.ar(\out.kr(0), DirtPan.ar(sig, dirt.numChannels, \pan.kr(0)));
            }).add;
            dirt.soundLibrary.addSynth(synth, eventFor.(name));
        };
    };
    patches[\kit] !? { |kit|
        kit.do { |entry, i|
            var name = entry.asArray[0], extra = entry.asArray[1] ? ();
            // n chooses the drum, so it mustn't also retune it: pitch comes from
            // note and octave only (C5 when neither is set).
            var event = eventFor.(name, extra).put(\midinote, #{ (~note ? 0) + ((~octave ? 5) * 12) });
            dirt.soundLibrary.addSynth(\synkit, event, appendToExisting: i > 0);
        };
    };
    "selene soundset: % live synths%".format(
        voices.count { |v| v[\live] }, if(patches[\kit].notNil) { " + synkit" } { "" }).postln;
};
)
