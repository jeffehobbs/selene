// End-to-end check of a built pack in a real SuperDirt, silently.
// usage (build.py's `check` runs this): HOME=<tmp> sclang -l <conf.yaml> e2e.scd PACK_DIR OUT.wav
// Boots its own server on BlackHole (refuses to run without it, so nothing
// reaches the speakers), starts SuperDirt on port 57190, loads the pack the
// way Selene does (loadSoundFiles + synths.scd), plays a script of
// /dirt/play events, and records SuperDirt's output to OUT.wav.
// Prints "E2E EVENT <seconds> <label>" for each event and "E2E DONE".
(
var pack = thisProcess.argv[0], out = thisProcess.argv[1];
var device = "BlackHole 2ch", port = 57190;
var events = [
    // label, seconds from the start, /dirt/play key-value pairs
    ["synkit:0 kick", 0.5, [\s, \synkit, \n, 0]],
    ["synkit:1 snare", 1.0, [\s, \synkit, \n, 1]],
    ["synkit:2 hat", 1.5, [\s, \synkit, \n, 2]],
    ["synkit:3 open hat", 2.0, [\s, \synkit, \n, 3]],
    ["synkit:4 clap", 2.5, [\s, \synkit, \n, 4]],
    ["synkit:5 tom", 3.0, [\s, \synkit, \n, 5]],
    ["synkit:6 metal", 3.5, [\s, \synkit, \n, 6]],
    ["synkit:7 zap", 4.5, [\s, \synkit, \n, 7]],
    ["synhat", 5.5, [\s, \synhat]],
    ["synkick decay 1.2", 6.0, [\s, \synkick, \decay, 1.2]],
    ["synkick decay 1.2 fdecay 0.25", 7.5, [\s, \synkick, \decay, 1.2, \fdecay, 0.25]],
    ["syntom n 0", 8.5, [\s, \syntom, \n, 0]],
    ["syntom n 12", 9.5, [\s, \syntom, \n, 12]],
    ["synkit:5 n 5 (tom; n must not retune)", 10.5, [\s, \synkit, \n, 5]],
    ["synkit:5 note 12", 11.5, [\s, \synkit, \n, 5, \note, 12]],
    ["synhat voice 0", 12.5, [\s, \synhat, \voice, 0]],
    ["synhat voice 1", 13.0, [\s, \synhat, \voice, 1]],
    ["synhat voice 0 fvoice 1", 13.5, [\s, \synhat, \voice, 0, \fvoice, 1]],
    ["modkick:3 sample", 14.0, [\s, \modkick, \n, 3]],
    ["modtom:15 sample", 14.5, [\s, \modtom, \n, 15]],
    ["synkick decay 1.2 on a 16th step", 15.0, [\s, \synkick, \decay, 1.2, \delta, 0.125]],
    ["synkick decay 1.2 sustain 0.1 (choked)", 16.5, [\s, \synkick, \decay, 1.2, \sustain, 0.1]],
    ["synkit:3 open hat decay 0.9", 17.0, [\s, \synkit, \n, 3, \decay, 0.9]],
];
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
    dirt.loadSoundFiles(pack +/+ "samples/*");
    s.sync;
    dirt.start(port, [0]);
    (pack +/+ "synths.scd").load;
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
    3.wait;
    s.stopRecording;
    1.wait;
    "E2E DONE".postln;
    s.quit;
    0.exit;
};
)
