"""Tab in tTerm's own input line, and the lists behind its directory and
branch buttons.

The input line is the page's, not the shell's, so the shell's own completion
never sees it. What a word can become is asked of the machine itself, over a
quiet side channel of the terminal's SSH connection: a small sh script that
lists a directory, the commands on the PATH or the git branches. It never
touches the terminal: not its screen, not its history.

The line is read the way the shell would read it, closely enough to know
which word the cursor is in, what its value is with quotes and backslashes
taken off, and what kind of word it is: a command, a file, a directory, a
branch. The word is then written back with the same kind of quoting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Printed by the side scripts before their list: whatever a chatty startup
#: file prints first is cut off at it.
LIST_MARK = "__tterm_list__"
#: A list never gets longer than this; Tab on an empty word in /usr/bin
#: should not ship the whole directory.
MAX_LIST = 4000
#: What the page gets for one Tab, at most.
MAX_ITEMS = 500

#: Words that run the next word as a command.
PREFIXES = {"sudo", "doas", "time", "nohup", "nice", "env", "exec", "command",
            "builtin", "watch", "xargs", "stdbuf", "timeout", "strace"}

#: Builtins and keywords: commands with no file on the PATH.
BUILTINS = sorted({
    "alias", "bg", "bind", "break", "builtin", "case", "cd", "command",
    "compgen", "complete", "continue", "declare", "dirs", "disown", "echo",
    "enable", "eval", "exec", "exit", "export", "false", "fc", "fg", "for",
    "function", "getopts", "hash", "help", "history", "if", "jobs", "kill",
    "let", "local", "logout", "popd", "printf", "pushd", "pwd", "read",
    "readonly", "return", "select", "set", "shift", "shopt", "source",
    "suspend", "test", "time", "times", "trap", "true", "type", "typeset",
    "ulimit", "umask", "unalias", "unset", "until", "wait", "while",
})

GIT_COMMANDS = sorted({
    "add", "bisect", "blame", "branch", "checkout", "cherry-pick", "clean",
    "clone", "commit", "config", "diff", "fetch", "grep", "init", "log",
    "merge", "mv", "pull", "push", "rebase", "reflog", "remote", "reset",
    "restore", "revert", "rm", "show", "stash", "status", "submodule",
    "switch", "tag", "worktree",
})
#: git subcommands whose argument is a branch, and those where it may also
#: be a file.
GIT_BRANCH = {"switch", "merge", "rebase", "branch", "cherry-pick"}
GIT_BRANCH_OR_FILE = {"checkout", "diff", "log", "reset", "show", "restore"}

_SEPARATORS = set("|&;()\n`{}")
_SPECIAL = set(" \t\n'\"\\$`!*?;&|<>()[]{}#")


@dataclass
class Word:
    """The word under the cursor."""
    start: int              # where it starts in the line
    value: str = ""         # as the shell would read it
    quote: str = ""         # ' or " when it opened a quote not yet closed
    redirect: bool = False  # it follows < or >


@dataclass
class Context:
    kind: str               # command, file, dir, branch, branch+file, git, none
    word: Word
    command: str = ""       # the command the word belongs to
    switch: bool = False    # git switch: remote branches by their short name
    words: list[str] = field(default_factory=list)


def split(text: str) -> tuple[list[str], Word]:
    """The finished words of the command the end of `text` is in, and the
    word being typed at the end (empty if the text ends in a space)."""
    words: list[str] = []
    cur: list[str] = []
    start: int | None = None
    quote = ""
    redirect = False
    word_redirect = False
    i = 0

    def finish() -> None:
        nonlocal start, cur
        if start is not None:
            if not word_redirect:
                words.append("".join(cur))
            start, cur = None, []

    while i < len(text):
        c = text[i]
        if quote == "'":
            if c == "'":
                quote = ""
            else:
                cur.append(c)
            i += 1
            continue
        if quote == '"':
            if c == '"':
                quote = ""
            elif c == "\\" and i + 1 < len(text) and text[i + 1] in '"\\$`':
                cur.append(text[i + 1])
                i += 1
            else:
                cur.append(c)
            i += 1
            continue
        if c == "\\":
            if start is None:
                start, word_redirect, redirect = i, redirect, False
            if i + 1 < len(text):
                if text[i + 1] != "\n":
                    cur.append(text[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c in "'\"":
            if start is None:
                start, word_redirect, redirect = i, redirect, False
            quote = c
            i += 1
            continue
        if c in " \t":
            finish()
            i += 1
            continue
        if c == "$" and text[i + 1:i + 2] == "(":
            finish()
            words = []
            i += 2
            continue
        if c in _SEPARATORS:
            finish()
            words = []
            redirect = False
            i += 1
            continue
        if c in "<>":
            finish()
            redirect = True
            i += 1
            while i < len(text) and text[i] in "<>&|":
                i += 1
            continue
        if start is None:
            start, word_redirect, redirect = i, redirect, False
        cur.append(c)
        i += 1
    if start is None:
        return words, Word(start=len(text), redirect=redirect)
    return words, Word(start=start, value="".join(cur), quote=quote,
                       redirect=word_redirect)


def context(words: list[str], word: Word) -> Context:
    """What kind of word is being typed."""
    if word.value.startswith("$"):
        return Context("none", word)
    if word.redirect:
        return Context("file", word)
    rest = list(words)
    while rest and "=" in rest[0] and not rest[0].startswith("="):
        rest.pop(0)                     # FOO=bar cmd
    while rest and rest[0] in PREFIXES:
        rest.pop(0)                     # sudo, time, env...
        while rest and rest[0].startswith("-"):
            rest.pop(0)
    if not rest:
        if "/" in word.value or word.value.startswith(("~", ".")):
            return Context("file", word)
        return Context("command", word)
    command = rest[0].rsplit("/", 1)[-1]
    if word.value.startswith("-"):
        return Context("none", word, command)
    if command in ("cd", "pushd", "rmdir"):
        return Context("dir", word, command)
    if command == "git":
        args = [w for w in rest[1:] if not w.startswith("-")]
        if not args:
            return Context("git", word, command)
        sub = args[0]
        if sub in GIT_BRANCH:
            return Context("branch", word, command, switch=sub == "switch")
        if sub in GIT_BRANCH_OR_FILE:
            return Context("branch+file", word, command)
    return Context("file", word, command, words=rest)


# ------------------------------------------------------------- quoting


def escape(value: str, quote: str = "") -> str:
    """A value written back as the shell reads it, in the word's quoting."""
    if quote == "'":
        return value.replace("'", "'\\''")
    if quote == '"':
        return "".join("\\" + c if c in '"\\$`' else c for c in value)
    out = []
    for i, c in enumerate(value):
        if c in _SPECIAL or (c == "~" and i > 0):
            out.append("\\" + c)
        else:
            out.append(c)
    return "".join(out)


def word_text(value: str, quote: str) -> str:
    """A whole word: the opening quote, if the word had one, then the value."""
    return quote + escape(value, quote)


def sh_quote(value: str) -> str:
    """For the side scripts: one argument, safe in any POSIX sh."""
    return "'" + value.replace("'", "'\\''") + "'"


# ----------------------------------------------------------- the scripts


def list_script(cwd: str, path: str) -> str:
    """Lists a directory, relative to the terminal's: one name a line,
    directories with a / after them (symlinks to one too). A relative path
    from a directory that cannot be entered lists nothing, rather than the
    wrong place."""
    return (
        f"set -- {sh_quote(cwd)} {sh_quote(path)}\n"
        'if ! cd -- "$1" 2>/dev/null; then\n'
        '  case $2 in /*) ;; *) exit 0 ;; esac\n'
        "fi\n"
        f"printf '%s\\n' {LIST_MARK}\n"
        f'ls -1ApL -- "${{2:-.}}" 2>/dev/null | head -n {MAX_LIST}\n'
    )


def commands_script(path: str, prefix: str) -> str:
    """The commands on the PATH that start with `prefix`: files one may run.
    The PATH is the one the terminal's shell has, from its last prompt."""
    return (
        f"set -- {sh_quote(path)} {sh_quote(prefix)}\n"
        '[ -n "$1" ] || set -- "$PATH" "$2"\n'
        f"printf '%s\\n' {LIST_MARK}\n"
        "printf '%s\\n' \"$1\" | tr ':' '\\n' | while IFS= read -r d; do\n"
        '  [ -n "$d" ] && [ -d "$d" ] || continue\n'
        '  for f in "$d"/"$2"*; do\n'
        '    [ -f "$f" ] && [ -x "$f" ] && printf \'%s\\n\' "${f##*/}"\n'
        "  done\n"
        f"done | head -n {MAX_LIST}\n"
    )


def branches_script(cwd: str) -> str:
    """The branches of the repository the terminal is in, local and remote."""
    return (
        f"set -- {sh_quote(cwd)}\n"
        'cd -- "$1" 2>/dev/null || exit 0\n'
        f"printf '%s\\n' {LIST_MARK}\n"
        "git for-each-ref --format='%(refname)' refs/heads refs/remotes "
        f"2>/dev/null | head -n {MAX_LIST}\n"
    )


#: A name with a control character in it is left out of every list: a \r
#: would turn into a second line, and so into a second command.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def listed(output: bytes) -> list[str]:
    """The lines a side script printed after its mark, without names that
    carry control characters."""
    text = output.decode("utf-8", "replace")
    _, sep, after = text.partition(LIST_MARK + "\n")
    if not sep:
        return []
    return [line for line in after.split("\n")
            if line and not _CONTROL.search(line)]


# ------------------------------------------------------------- the items


def dir_part(value: str, home: str) -> tuple[str, str, str]:
    """Splits a path word: the directory to list (as the side script gets
    it), the part of the value kept as typed, and the name being typed."""
    head, sep, name = value.rpartition("/")
    if not sep:
        if value == "~":
            return home or "~", "~/", ""
        return ".", "", value
    kept = head + "/"
    where = kept
    if kept == "~/" or kept.startswith("~/"):
        where = (home or "~") + kept[1:]
    return where or "/", kept, name


def file_items(word: Word, entries: list[str], home: str,
               dirs_only: bool = False) -> list[dict]:
    _, kept, name = dir_part(word.value, home)
    hidden = name.startswith(".")
    items = []
    for entry in entries:
        is_dir = entry.endswith("/")
        base = entry[:-1] if is_dir else entry
        if not base or base in (".", ".."):
            continue
        if not base.startswith(name):
            continue
        if base.startswith(".") and not hidden:
            continue
        if dirs_only and not is_dir:
            continue
        value = kept + base + ("/" if is_dir else "")
        items.append({"text": word_text(value, word.quote),
                      "label": base + ("/" if is_dir else ""),
                      "kind": "dir" if is_dir else "file"})
    items.sort(key=lambda i: (i["kind"] != "dir", i["label"].lower()))
    return items


def command_items(word: Word, names: list[str]) -> list[dict]:
    seen = set()
    items = []
    for name in sorted(set(names) | set(BUILTINS)):
        if not name.startswith(word.value) or name in seen:
            continue
        seen.add(name)
        items.append({"text": word_text(name, word.quote), "label": name,
                      "kind": "command"})
    return items


def branch_items(word: Word, refs: list[str], switch: bool = False) -> list[dict]:
    local, remote = [], []
    for ref in refs:
        if ref.startswith("refs/heads/"):
            local.append(ref[len("refs/heads/"):])
        elif ref.startswith("refs/remotes/"):
            name = ref[len("refs/remotes/"):]
            if name.endswith("/HEAD"):
                continue
            remote.append(name)
    names = list(local)
    if switch:
        # `git switch feature` makes a local branch from origin/feature.
        names += [r.split("/", 1)[1] for r in remote if "/" in r]
    else:
        names += remote
    items, seen = [], set()
    for name in names:
        if name in seen or not name.startswith(word.value):
            continue
        seen.add(name)
        items.append({"text": word_text(name, word.quote), "label": name,
                      "kind": "branch"})
    return items


def git_items(word: Word) -> list[dict]:
    return [{"text": word_text(c, word.quote), "label": c, "kind": "command"}
            for c in GIT_COMMANDS if c.startswith(word.value)]


def dirs_of(entries: list[str]) -> list[str]:
    """The directories in a listing, hidden ones last."""
    names = [e[:-1] for e in entries if e.endswith("/") and e not in ("./", "../")]
    return sorted(names, key=lambda n: (n.startswith("."), n.lower()))


def branches_of(refs: list[str]) -> dict:
    local = [r[len("refs/heads/"):] for r in refs if r.startswith("refs/heads/")]
    remote = [r[len("refs/remotes/"):] for r in refs
              if r.startswith("refs/remotes/") and not r.endswith("/HEAD")]
    return {"local": sorted(local, key=str.lower),
            "remote": sorted(remote, key=str.lower)}


def cd_command(target: str) -> str:
    """`cd` into a directory, written the plainest way that is still safe."""
    if target == "..":
        return "cd .."
    if target and all(c not in _SPECIAL for c in target) \
            and not target.startswith("-"):
        return f"cd {target}"
    if target.startswith("-"):
        target = "./" + target
    return f"cd {sh_quote(target)}"


def switch_command(branch: str) -> str:
    if branch and all(c not in _SPECIAL for c in branch):
        return f"git switch {branch}"
    return f"git switch {sh_quote(branch)}"
