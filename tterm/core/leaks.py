"""Secrets cut out of what goes to an AI (0.1.29, PROJECT.md «ИИ через MCP»).

What a terminal printed, a command, a line of a log or a note goes to the
company whose AI reads it. Before it goes, what looks like a secret is cut:
private keys, tokens of the known kinds (GitHub, GitLab, Slack, Stripe,
OpenAI, Anthropic, AWS, Google, npm, PyPI, DigitalOcean, SendGrid, Brevo,
Telegram, Hugging Face, Shopify, JWT, tTerm's own), passwords in addresses,
the values of variables, keys and options named as secrets, of
authorization headers and cookies, and the passwords of mysql, curl and
sshpass on the command line.

It is a net with holes, as any is: a password typed in plain words, or a
token of a kind nobody knows, goes through. It does not make reading safe;
it keeps the common accidents out of somebody else's logs. The person
decides what the AI may read: the machines, their terminals, their logs.
"""
from __future__ import annotations

import re

#: What stands where a secret was.
CUT = "[secret cut by tTerm]"

#: Private keys, whole: PEM blocks of any kind of private key, and PuTTY's
#: private lines. A block whose end is not there (the output was cut) is cut
#: to the end of the text.
_PEM = re.compile(
    r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----"
    r"(?:.*?-----END \1-----|.*\Z)", re.DOTALL)
_PUTTY = re.compile(r"(Private-Lines: *\d+)\r?\n(?:[A-Za-z0-9+/=]+(?:\r?\n|\Z))+")

#: Tokens that say what they are by how they begin.
_TOKENS = re.compile(
    r"\b(?:"
    r"gh[pousr]_[A-Za-z0-9]{30,255}"
    r"|github_pat_[A-Za-z0-9_]{22,255}"
    r"|glpat-[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|xapp-\d-[A-Za-z0-9-]{10,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|whsec_[A-Za-z0-9]{20,}"
    r"|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}"
    r"|(?:AKIA|ASIA)[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|ya29\.[0-9A-Za-z_-]{20,}"
    r"|npm_[A-Za-z0-9]{36}"
    r"|pypi-[A-Za-z0-9_-]{50,}"
    r"|do[opr]_v1_[a-f0-9]{64}"
    r"|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}"
    r"|xkeysib-[a-f0-9]{64}-[A-Za-z0-9]{16}"
    r"|\d{8,10}:AA[A-Za-z0-9_-]{30,}"
    r"|hf_[A-Za-z0-9]{30,}"
    r"|shp(?:at|ca|pa|ss)_[a-fA-F0-9]{32}"
    r"|tterm_(?:at|rt|ac|ak|cs)_[A-Za-z0-9_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")")

#: A password in an address: scheme://user:password@host keeps the user.
_URL_PASSWORD = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@")

#: A variable, option or key named as a secret, and its value: FOO_TOKEN=…,
#: "password": "…", --api-key …, aws_secret_access_key = ….
_NAMES = (r"[A-Za-z0-9_.-]*(?:password|passwd|passphrase|secret|token"
          r"|api[_-]?key|access[_-]?key|private[_-]?key|credentials?"
          r"|auth[_-]?key)[A-Za-z0-9_.-]*")
_ASSIGNED = re.compile(
    r"(?i)(?P<name>[\"']?\b" + _NAMES + r"[\"']?)(?P<eq>\s*[:=]\s*)"
    r"(?P<q>[\"']?)(?P<value>[^\s\"',;}{&]{3,})(?P=q)")
_OPTION = re.compile(
    r"(?i)(?P<name>(?<![\w-])--?" + _NAMES + r")(?P<eq>=|\s+)(?P<q>[\"']?)"
    r"(?P<value>[^\s\"']{3,})(?P=q)")

#: Authorization headers and cookies, as curl -v, -H and HTTP dumps show
#: them; in quotes, to the closing quote.
_HEADER = re.compile(
    r"(?im)\b(?P<name>authorization|proxy-authorization|x-api-key|api-key"
    r"|x-auth-token|x-access-token|cookie|set-cookie)(?P<sep>\s*:\s*)"
    r"(?P<scheme>(?:bearer|basic|token|digest)\s+)?(?P<value>[^\r\n\"']+)")

#: Passwords on the command line: mysql -pSECRET, curl -u user:SECRET,
#: sshpass -p SECRET.
_MYSQL = re.compile(
    r"(\b(?:mysql|mysqldump|mysqladmin|mariadb|mariadb-dump)\b[^\n]*?\s-p)"
    r"(?:'[^'\n]*'|\"[^\"\n]*\"|[^\s'\"]+)")
_CURL_USER = re.compile(
    r"(\b(?:curl|wget|http|https)\b[^\n]*?\s(?:-u|--user|--http-user)[\s=]+"
    r"[\"']?[^\s:\"']+):([^\s\"']+)")
_SSHPASS = re.compile(r"(\bsshpass\s+-p\s*)(?:'[^'\n]*'|\"[^\"\n]*\"|\S+)")


def cut(text: str) -> str:
    """The text with what looks like a secret in it cut out."""
    if not text:
        return text
    text = _PEM.sub(lambda m: f"-----BEGIN {m.group(1)}-----\n{CUT}\n"
                    f"-----END {m.group(1)}-----", text)
    text = _PUTTY.sub(lambda m: f"{m.group(1)}\n{CUT}\n", text)
    text = _TOKENS.sub(CUT, text)
    text = _URL_PASSWORD.sub(lambda m: f"{m.group(1)}:{CUT}@", text)
    text = _HEADER.sub(_header, text)
    text = _MYSQL.sub(lambda m: f"{m.group(1)}{CUT}", text)
    text = _CURL_USER.sub(lambda m: f"{m.group(1)}:{CUT}", text)
    text = _SSHPASS.sub(lambda m: f"{m.group(1)}{CUT}", text)
    text = _OPTION.sub(_keep_name, text)
    text = _ASSIGNED.sub(_keep_name, text)
    return text


def _cut_already(value: str) -> bool:
    """A value that is a cut already, or its first word: as it was."""
    return value == CUT or CUT.startswith(value)


def _header(m: re.Match) -> str:
    if _cut_already(m.group("value").strip()):
        return m.group(0)
    return (f"{m.group('name')}{m.group('sep')}{m.group('scheme') or ''}{CUT}")


def _keep_name(m: re.Match) -> str:
    """The name and how it was given, the value cut."""
    if _cut_already(m.group("value")):
        return m.group(0)
    q = m.group("q")
    return f"{m.group('name')}{m.group('eq')}{q}{CUT}{q}"
