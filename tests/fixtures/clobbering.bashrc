#!/usr/bin/env bash
# A shell whose startup file takes the prompt machinery back.
#
# An interactive bash sources this file and then blocks in its own read loop:
# bash never reaches the prompt cycle our injected OSC 133 hooks live in, so no
# marker can be emitted. The wrapper prints its own prompt and evals each line.
# This is the shape that makes every fact-level wait unsatisfiable until `send`
# supplies the marker itself.
PS1=''
PS0=''
PROMPT_COMMAND=''
__tu_clobber_prompt() { printf 'tu-clobber$ '; }
__tu_clobber_prompt
while IFS= read -r __tu_line; do
    eval "$__tu_line"
    __tu_clobber_prompt
done
