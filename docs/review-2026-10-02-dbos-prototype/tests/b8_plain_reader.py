"""B8 reader: plain sqlite3 + json, NO DBOS (run with the system python, where `import dbos` fails).

usage: b8_plain_reader.py <sys.sqlite> <rid>
Reads the run's per-step values and status the way a non-Python tool would have to.
"""

import base64
import json
import sqlite3
import sys

try:
    import dbos  # noqa: F401

    print("WARNING: dbos importable here")
except ImportError:
    print("dbos is NOT importable in this interpreter:", sys.executable)

db, rid = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
print("dbos_migrations:", c.execute("SELECT * FROM dbos_migrations").fetchall())


def decode(text, ser):
    if ser in (None, "py_pickle"):
        raw = base64.b64decode(text)
        return f"<py_pickle, {len(raw)} bytes, protocol {raw[1]}: not JSON; needs Python pickle>"
    return json.loads(text)


segs = c.execute(
    "SELECT s.workflow_uuid, s.status, s.serialization, o.output, s.updated_at FROM workflow_status s"
    " LEFT JOIN workflow_output o USING (workflow_uuid) WHERE s.workflow_uuid LIKE ? ORDER BY s.created_at",
    (rid + ".%",),
).fetchall()
by_step = {}
for wid, status, ser, output, upd in segs:
    print(
        f"segment {wid}: status={status} workflow serialization={ser} output={decode(output, ser) if output else None}"
    )
    for off, val, vser in c.execute(
        'SELECT "offset", value, serialization FROM streams WHERE workflow_uuid=? AND key=?'
        ' ORDER BY "offset"',
        (wid, "loss"),
    ):
        v = decode(val, vser)
        if isinstance(v, dict):
            by_step[v["step"]] = v
        elif off < 2:
            print(f"   stream offset {off}: serialization={vser} -> {v}")
    for fid, name, out, oser in c.execute(
        "SELECT function_id, function_name, output, serialization FROM operation_outputs"
        " WHERE workflow_uuid=? ORDER BY function_id LIMIT 3",
        (wid,),
    ):
        print(
            f"   step row {fid} {name}: serialization={oser} -> {decode(out, oser) if out else None}"
        )
    for key, val, eser in c.execute(
        "SELECT key, value, serialization FROM workflow_events WHERE workflow_uuid=?",
        (wid,),
    ):
        print(f"   event {key}: serialization={eser} -> {decode(val, eser)}")
if by_step:
    steps = sorted(by_step)
    print(
        f"values read with sqlite3+json: {len(steps)} steps {steps[0]}..{steps[-1]}; step 3 = {by_step.get(3)}"
    )
try:
    print("beacon:", c.execute("SELECT wid, pid, round(t,1) FROM rs_beacon").fetchall())
except sqlite3.OperationalError as e:
    print("beacon:", e)
