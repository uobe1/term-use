"""Shell integration injection: rc files that emit OSC 133 command markers.

bash layout (VSCode template adapted):
  PROMPT_COMMAND first entry reads $?  -> D;<code> then A (next prompt)
  PS0                                  -> C (pre-execution)
  PS1 wrapped in \\[ \\]                -> B (command line start)

zsh layout: `add-zsh-hook precmd/preexec` + a PS1 prefix, delivered through a
ZDOTDIR that *layers* on top of the user's own startup files.

`bash --rcfile` (and a ZDOTDIR override) replace the user's startup files
entirely, so the injected file takes responsibility for the environment: it
re-sources the machine's own setup by *convention* (sysconf paths, a
`$PREFIX`-style install prefix, then ~/.bashrc / ~/.zshrc) rather than
hardcoding one device's layout, and it installs our hooks **after** that
sourcing so a user PROMPT_COMMAND/PS1/precmd is wrapped, not clobbered.

fish needs nothing from here: fish >= 4.0 emits OSC 133 on its own and
`records.detect_shell_integration()` notices.
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


# zsh records the original ZDOTDIR in USER_ZDOTDIR before honouring ours, so
# every shim can source the user's real file first and add ours on top.
# (__FILE__ is substituted by hand: shell `${VAR}` braces collide with str.format.)
_ZSH_LAYER = (
    '__tu_user_zdotdir="${USER_ZDOTDIR:-$HOME}"',
    '[ -f "$__tu_user_zdotdir/__FILE__" ] && . "$__tu_user_zdotdir/__FILE__"',
    'unset __tu_user_zdotdir',
)

_ZSH_HOOKS = (
    "# --- term-use v2 shell integration (OSC 133) ---",
    'if [ -z "${__TU_ZSH_INTEGRATION:-}" ]; then',
    '  export __TU_ZSH_INTEGRATION=1',
    "  autoload -U add-zsh-hook",
    "  __tu_precmd() {",
    "    local __tu_st=$?           # must be the first line: the exit code",
    "    printf '\\e]133;D;%s\\a\\e]133;A\\a' \"$__tu_st\"",
    "  }",
    "  __tu_preexec() { printf '\\e]133;C\\a'; }",
    "  add-zsh-hook precmd __tu_precmd",
    "  add-zsh-hook preexec __tu_preexec",
    '  PS1="%{$(printf \'\\e]133;B\\a\')%}$PS1"',
    "fi",
)


def zsh_integration_files() -> dict[str, str]:
    """Contents for a ZDOTDIR directory: user files first, our hooks last.

    HISTFILE is restored to the user's location — a ZDOTDIR override silently
    relocates history otherwise, which is exactly the kind of side effect that
    makes people distrust injected configuration.
    """
    out: dict[str, str] = {}
    for fname in ("zshenv", "zprofile", "zlogin"):
        out[fname] = "\n".join(line.replace("__FILE__", "." + fname)
                               for line in _ZSH_LAYER) + "\n"
    zshrc = ["# term-use zsh integration: layer over the user's own .zshrc",
             '__tu_user_zdotdir="${USER_ZDOTDIR:-$HOME}"',
             '[ -f "$__tu_user_zdotdir/.zshrc" ] && . "$__tu_user_zdotdir/.zshrc"',
             'export HISTFILE="$__tu_user_zdotdir/.zsh_history"',
             'unset __tu_user_zdotdir',
             *_ZSH_HOOKS, ""]
    out["zshrc"] = "\n".join(zshrc)
    return out
