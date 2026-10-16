"""Shell integration injection: a bashrc that emits OSC 133 markers.

Layout (VSCode template adapted):
  PROMPT_COMMAND first entry reads $?  -> D;<code> then A (next prompt)
  PS0                                  -> C (pre-execution)
  PS1 wrapped in \\[ \\]                -> B (command line start)

`bash --rcfile` replaces the user's startup files entirely, so the injected
rcfile takes responsibility for the environment: it re-sources the machine's
own bash setup by *convention* (XDG/sysconf paths, a `$PREFIX`-style install
prefix, then the user's ~/.bashrc) rather than hardcoding one device's layout.
The sourcing happens before our hooks are installed, so a user rcfile that sets
PROMPT_COMMAND/PS1 still gets wrapped, not clobbered.
"""

# Searched by bash itself, so the paths reflect the running environment
# (SYSCONFDIR is where a built-from-source or staged bash puts its system rc;
# PREFIX/SYSROOT are conda, Nix profiles, stow and Termux conventions).
_SOURCE_BLOCK = (
    '# --- term-use: re-source this machine\'s own bash setup ---',
    'if [ -z "${BASHRCSOURCED:-}" ]; then',
    '  for __tu_rc in "${SYSCONFDIR:+$SYSCONFDIR/bash.bashrc}" '
    '/etc/bash.bashrc /etc/bashrc '
    '"${PREFIX:+$PREFIX/etc/bash.bashrc}" "${SYSROOT:+$SYSROOT/etc/bash.bashrc}"; do',
    '    [ -r "$__tu_rc" ] && { . "$__tu_rc"; break; }',
    '  done',
    '  unset __tu_rc',
    'fi',
    '[ -r "$HOME/.bashrc" ] && . "$HOME/.bashrc"',
)


def bashrc_template() -> str:
    lines = [
        "# --- term-use v2 shell integration (OSC 133) ---",
        *_SOURCE_BLOCK,
        "__td_ps() { printf '\\033]133;D;%s\\007\\033]133;A\\007' \"$1\"; }",
        'case ":$PROMPT_COMMAND:" in',
        '  *"__td_ps"*) ;;  # already integrated',
        '  "::") PROMPT_COMMAND="__td_ps \\$?" ;;',
        '  *) PROMPT_COMMAND="__td_ps \\$?;${PROMPT_COMMAND:+\\$PROMPT_COMMAND}" ;;',
        "esac",
        'PS0="${PS0}\\e]133;C\\a"',
        'PS1="\\[\\e]133;B\\a\\]$PS1"',
        "",
    ]
    return "\n".join(lines)
