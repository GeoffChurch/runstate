"""B8 third-party read without DBOS: run the plain reader (system python, no dbos) on
(1) a layer-written run (portable_json serializer) and (2) a run written with DBOS defaults.
"""

import os
import shutil
import subprocess

from common import *

root = fresh_root("b8")
import rs_dbos as r

premigrate(root)
rid = r.run_id(CONFIG)
spawn(root, 12, {"RS_STEP_SECONDS": "0.01"}, tag="w").wait(60)
sysdb = os.path.join(root, "sys.sqlite")
plain = shutil.which("python3", path="/usr/bin:/bin")
print(f"== (1) layer-written run, read by {plain} ==")
p1 = subprocess.run(
    [plain, os.path.join(HERE, "b8_plain_reader.py"), sysdb, rid],
    capture_output=True,
    text=True,
)
print(p1.stdout, p1.stderr)

ddb = os.path.join(root, "default.sqlite")
subprocess.run(
    [PY, os.path.join(HERE, "b8_default_writer.py"), ddb],
    check=True,
    capture_output=True,
    cwd=root,
)
print("== (2) run written with DBOS's default serializer ==")
p2 = subprocess.run(
    [plain, os.path.join(HERE, "b8_plain_reader.py"), ddb, "defaultrun"],
    capture_output=True,
    text=True,
)
print(p2.stdout, p2.stderr)
ok1 = (
    "values read with sqlite3+json: 12 steps 0..11" in p1.stdout
    and "NOT importable" in p1.stdout
)
ok2 = "py_pickle" in p2.stdout
verdict_line(
    "B8 third-party read without DBOS",
    "PARTIAL" if ok1 else "FAIL",
    [
        f"layer run (portable_json configured): values + status readable with sqlite3+json, no DBOS: {ok1}",
        f"DBOS defaults: step outputs / workflow output / events / streams are base64 pickle: {ok2}",
        "schema: documented 'System Tables' page (Postgres schema; no stability promise for the tables); SQLite schema "
        "differs (no 'dbos.' schema prefix) and DBOS's own launch log calls SQLite 'for development and testing'",
    ],
)
