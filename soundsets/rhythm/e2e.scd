// The Rhythm pack's end-to-end check: events for ../e2e.scd, which plays
// them in a silent SuperDirt. build.py's EXPECT["rhythm"] says what each
// recording must show. Each event: label, seconds from the start, and
// /dirt/play key-value pairs (cps 0.5, delta 0.5 and orbit 0 unless given).
[
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
]
