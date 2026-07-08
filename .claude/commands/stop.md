---
description: Halt the council loop cleanly — writes .council/state/stop.flag so the next /council-cycle exits and /loop terminates.
argument-hint: "[optional reason]"
allowed-tools: Write, Read
---

The user wants to stop the council loop. Optional reason:

$ARGUMENTS

1. Write `.council/state/stop.flag` with a one-line reason: the arguments above if given, otherwise `user requested stop`.
2. Confirm in one line that the loop will halt at the next cycle boundary. Mention that `/goal` clears this user stop and starts a fresh run; ceiling stops may auto-resume when headroom is available.
