# experiments

| Project | Purpose |
|---|---|
| `AudioProbe` | Small ACS Call Automation app used to study real call audio: frame formats, unmixed participants, reconnects, timing and barge-in. Findings drove the design of `VoiceIntake` (24 kHz audio, per-speaker recognizers, reconnect handling) |

Not part of the product and not deployed in the reference environment. Safe to delete if you do not need it for
diagnostics. Build it with its `Dockerfile` the same way as `VoiceIntake`.
