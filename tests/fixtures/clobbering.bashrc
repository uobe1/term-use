#!/usr/bin/env bash
# A shell whose startup file takes the prompt machinery back.
#
# The shape that kills term-use's injected OSC 133 integration: not a theme that
# merely colors the prompt, but one that owns `PROMPT_COMMAND`/`PS0`/the read loop
# itself, so bash never runs the cycle our hooks live in. Measured on the
# reporter's device, where ~/.bashrc ends with a wrapper loop that clears PS1 and
# PROMPT_COMMAND and reads lines in its own program — every fact-level wait there
# became unsatisfiable, and no marker ever reached the recording.
#
# Kept minimal and POSIX: an interactive bash gets this as its --rcfile.
PS1='tu-clobber$ '
PS0=''
PROMPT_COMMAND=''
TMUX_FAKE_THEME=1
