#!/usr/bin/env python3
"""Keep a fixed number of rollouts running at once, across models.

run.sh runs one model's rollouts together and the next model only after the slowest of
them ends, so slots sit idle. This starts each rollout as its own `./run.sh <model> 1`
(their runner, one epoch) and launches the next whenever fewer than `slots` rollout
containers are running, counting containers from any run on this Docker host.

    ARM=original ./run_pool.py "fable sol opus" 10          # ten rollouts per model, ten at a time
    ARM=original ./run_pool.py "fable sol opus" 10 8        # eight at a time
    ARM=original ./run_pool.py "fable=17,astra=10 sol=7,opus=8" 0 10
        # priority groups, in order: fable and astra alternate until both are done, then sol and opus
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = {**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")}
STARTUP_GRACE_S = 90        # a launched rollout's container can take this long to appear


def running_containers() -> int:
    out = subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True, env=ENV).stdout
    return len(out.split())


def main() -> int:
    spec, per = sys.argv[1], int(sys.argv[2])
    slots = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    if "=" in spec:
        queue = []
        for group in spec.split():                       # groups run in order; models alternate within one
            counts = {m: int(n) for m, n in (item.split("=") for item in group.split(","))}
            while any(counts.values()):
                for m in counts:
                    if counts[m]:
                        queue.append(m); counts[m] -= 1
        models = list(dict.fromkeys(queue))
    else:
        models = spec.split()
        queue = [m for _ in range(per) for m in models]  # interleaved: fable, sol, opus, fable, ...
    logs = HERE / ("logs-original" if os.environ.get("ARM") == "original" else "logs")
    logs.mkdir(exist_ok=True)
    note = lambda text: (logs / "launches.txt").open("a").write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} pool: {text}\n")
    note(f"start {len(queue)} rollouts of {' '.join(models)}, {slots} at a time")
    procs: list[subprocess.Popen] = []
    launched: list[float] = []
    while queue or any(p.poll() is None for p in procs):
        now = time.monotonic()
        pending = sum(1 for t in launched if now - t < STARTUP_GRACE_S)
        if queue and running_containers() + pending < slots:
            model = queue.pop(0)
            procs.append(subprocess.Popen(["./run.sh", model, "1"], cwd=HERE, env=ENV,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            launched.append(now)
            continue
        time.sleep(10)
    failed = sum(1 for p in procs if p.returncode)
    note(f"end: {len(procs)} rollouts, {failed} exited non-zero")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
