// Used only when the user's startup.scd doesn't start SuperDirt itself.
(
s.options.numBuffers = 1024 * 256;
s.options.memSize = 8192 * 32;
s.options.numWireBufs = 2048;
s.options.maxNodes = 1024 * 32;
s.latency = 0.3;
s.waitForBoot {
    ~dirt.stop;
    ~dirt = SuperDirt(2, s);
    ~dirt.loadSoundFiles;
    s.sync;
    ~dirt.start(57120, 0 ! 12);
    SuperDirt.default = ~dirt;
};
);
