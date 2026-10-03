"""Worker CLI: python worker.py '<config json>' <until>   (env: RS_ROOT, RS_CHUNK, RS_PEEK, ...)"""

import json
import os
import sys
import time

import rs_dbos
from sim import SimTrainer

if __name__ == "__main__":
    config, until = json.loads(sys.argv[1]), int(sys.argv[2])
    rs_dbos.TRAINER = SimTrainer(rs_dbos.ROOT)
    t0 = time.time()
    out = rs_dbos.run(
        config,
        until,
        chunk=int(os.environ.get("RS_CHUNK", "10")),
        peek=os.environ.get("RS_PEEK") == "1",
    )
    print(
        f"[{os.getpid()}] RESULT {json.dumps(out)} after {time.time() - t0:.2f}s",
        flush=True,
    )
    os._exit(0)  # DBOS keeps background threads; exit promptly
