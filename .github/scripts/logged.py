"""Voert een opdracht uit en zet bij een fout de laatste uitvoerregels als annotatie in GitHub.
(Annotaties zijn via de API leesbaar, ook als de volledige log niet te downloaden is.)

    python .github/scripts/logged.py "<titel>" -- <opdracht> [argumenten…]
Met --warn als eerste argument wordt een fout als waarschuwing gemeld (voor niet-blokkerende stappen)."""
import collections
import subprocess
import sys

args = sys.argv[1:]
level = "error"
if args and args[0] == "--warn":
    level, args = "warning", args[1:]
title, sep, cmd = args[0], args[1], args[2:]
assert sep == "--"
tail = collections.deque(maxlen=60)
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                        errors="replace", bufsize=1)
for line in proc.stdout:
    sys.stdout.write(line)
    sys.stdout.flush()
    tail.append(line.rstrip("\n"))
rc = proc.wait()
if rc != 0:
    msg = "\n".join(tail).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level} title={title} (code {rc})::{msg}", flush=True)
# ook bij succes de laatste regels van tests/rooktest tonen als notitie (resultaatoverzicht)
elif title.startswith(("Tests", "Rooktest")):
    msg = "\n".join(list(tail)[-25:]).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title={title}::{msg}", flush=True)
sys.exit(rc)
