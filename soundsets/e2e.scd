// Play built packs in a real SuperDirt, silently, and record it.
// usage (build.py runs this): HOME=<tmp> sclang -l <conf.yaml> e2e.scd OUT.wav EVENTS.scd PACK_DIR...
// Boots its own server on BlackHole (refuses to run without it, so nothing
// reaches the speakers), starts SuperDirt on port 57190, loads each pack the
// way Selene does (loadSoundFiles + synths.scd), plays EVENTS.scd's
// /dirt/play events, and records SuperDirt's output to OUT.wav.
// EVENTS.scd returns [[label, seconds, pairs], ...] or (events: [...], tail: seconds).
// Prints "E2E EVENT <seconds> <label>" for each event and "E2E DONE".
(
var argv = thisProcess.argv, out = argv[0], spec = argv[1].load, packs = argv.drop(2);
var events = if(spec.isKindOf(Event)) { spec[\events] } { spec };
var tail = if(spec.isKindOf(Event)) { spec[\tail] ? 3 } { 3 };
var device = "BlackHole 2ch", port = 57190;

if(ServerOptions.outDevices.indexOfEqual(device).isNil) {
    "E2E ABORT: % not found; refusing to play through the speakers".format(device).postln;
    0.exit;
};
s = Server(\e2e, NetAddr("127.0.0.1", 57191));
Server.default = s;
s.options.outDevice = device;
s.options.inDevice = nil;
s.options.numInputBusChannels = 0;
s.options.numOutputBusChannels = 2;
s.options.numBuffers = 1024 * 16;
s.options.memSize = 8192 * 16;
s.latency = 0.2;
s.waitForBoot {
    var dirt = SuperDirt(2, s), addr = NetAddr("127.0.0.1", port), t0;
    ~dirt = dirt;
    packs.do { |pack| dirt.loadSoundFiles(pack +/+ "samples/*") };
    s.sync;
    dirt.start(port, [0, 0]);
    packs.do { |pack| (pack +/+ "synths.scd").load };
    s.sync;
    s.prepareForRecord(out, 2);
    s.sync;
    s.record;
    1.wait;
    t0 = Main.elapsedTime;
    events.do { |e|
        var label = e[0], at = e[1], pairs = e[2];
        (t0 + at - Main.elapsedTime).max(0).wait;
        addr.sendBundle(0.2, ['/dirt/play', \cps, 0.5, \cycle, at, \delta, 0.5, \orbit, 0] ++ pairs);
        "E2E EVENT % %".format(at, label).postln;
    };
    tail.wait;
    s.stopRecording;
    1.wait;
    "E2E DONE".postln;
    s.quit;
    0.exit;
};
)
