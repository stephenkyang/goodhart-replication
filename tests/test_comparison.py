"""Exercise comparison counts, concurrency, phase ordering, and failed-run cleanup."""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent.parent / "goodhart-replication"
spec = importlib.util.spec_from_file_location("comparison", HERE / "run_comparison.py")
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def test_default_plan_is_twenty_per_model_per_prompt():
    plan = comparison.make_plan(["astra", "sol"], 20, 10, 20, 10)
    counts = Counter()
    for batch in plan["batches"]:
        counts[(batch["arm"], batch["model"])] += batch["epochs"]
    assert counts == {("modified", "astra"): 20, ("modified", "sol"): 20,
                      ("original", "astra"): 20, ("original", "sol"): 20}
    assert plan["total_rollouts"] == 80


def test_queue_enforces_global_and_per_model_limits_and_phase_barrier():
    plan = comparison.make_plan(["astra", "sol"], 20, 10, 20, 10)
    astra = comparison.eligible_job(plan, [])
    astra["status"] = "running"
    sol = comparison.eligible_job(plan, [astra])
    assert sol["model"] == "sol" and sol["arm"] == "modified"
    sol["status"] = "running"
    assert comparison.eligible_job(plan, [astra, sol]) is None
    # Even when all modified jobs have been launched, original waits for completion.
    for batch in plan["batches"]:
        if batch["arm"] == "modified":
            batch["status"] = "complete"
    astra["status"] = "running"
    assert comparison.eligible_job(plan, [astra]) is None
    astra["status"] = "complete"
    assert comparison.eligible_job(plan, [])["arm"] == "original"
    plan = comparison.make_plan(["astra", "sol"], 20, 10, 10, 10)
    job = comparison.eligible_job(plan, [])
    job["status"] = "running"
    assert comparison.eligible_job(plan, [job]) is None


def fake_runner(tmp_path, fail=False):
    script = tmp_path / "runner"
    script.write_text(f"#!{sys.executable}\n" +
        "import json, os, pathlib, sys, time\n"
        "root=pathlib.Path(os.environ['LOG_ROOT'])\n"
        "(root/'invocation.json').write_text(json.dumps({'arm':os.environ['ARM'],'model':sys.argv[1],'epochs':int(sys.argv[2])}))\n"
        "(root/'transcript.txt').write_text('simulated rollout')\n"
        "time.sleep(0.02)\n" + ("raise SystemExit(2)\n" if fail else ""))
    script.chmod(0o755)
    return script


def validate_fake(batch):
    return [{"graded": True, "used_engine": False, "completed": True,
             "restart": False, "result": "0-1", "limit": None}
            for _ in range(batch["epochs"])]


def test_real_child_processes_receive_correct_arm_counts_and_isolated_logs(tmp_path):
    plan = comparison.make_plan(["astra", "sol"], 3, 2, 4, 2)
    output = tmp_path / "comparison"
    comparison.run(plan, output, runner=fake_runner(tmp_path), validator=validate_fake, poll_seconds=0.005)
    saved = json.loads((output / "campaign.json").read_text())
    assert saved["status"] == "complete"
    assert sum(b["epochs"] for b in saved["batches"]) == 12
    assert len({b["log_root"] for b in saved["batches"]}) == 8
    for batch in saved["batches"]:
        call = json.loads((Path(batch["log_root"]) / "invocation.json").read_text())
        assert call == {"arm": "swapped" if batch["arm"] == "modified" else "original",
                        "model": batch["model"], "epochs": batch["epochs"]}
    modified_end = max(b["ended_at"] for b in saved["batches"] if b["arm"] == "modified")
    assert all(b["started_at"] >= modified_end for b in saved["batches"] if b["arm"] == "original")
    assert [g["episodes"] for g in json.loads((output / "summary.json").read_text())] == [3, 3, 3, 3]
    with pytest.raises(FileExistsError):
        comparison.run(plan, output, runner=fake_runner(tmp_path), validator=validate_fake)


def test_failure_stops_queue_and_removes_interrupted_transcripts(tmp_path):
    plan = comparison.make_plan(["astra", "sol"], 3, 2, 4, 2)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="runner exited"):
        comparison.run(plan, output, runner=fake_runner(tmp_path, fail=True),
                       validator=validate_fake, poll_seconds=0.005)
    saved = json.loads((output / "campaign.json").read_text())
    assert saved["status"] == "failed"
    assert all(b["status"] == "queued" for b in saved["batches"] if b["arm"] == "original")
    assert not list(output.rglob("transcript.txt"))
    assert not (output / "summary.json").exists()


@pytest.mark.parametrize("arm,image", [("swapped", "goodhart-replication:local"), ("original", "beat-stockfish:local")])
def test_shell_wrapper_passes_isolated_log_path_and_correct_image(tmp_path, arm, image):
    shutil.copy2(HERE / "run.sh", tmp_path / "run.sh")
    (tmp_path / ".env").write_text("")
    binary = tmp_path / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!{sys.executable}\nimport json,pathlib,sys\npathlib.Path('arguments.json').write_text(json.dumps(sys.argv[1:]))\n")
    binary.chmod(0o755)
    root = tmp_path / "isolated logs"
    subprocess.run(["sh", str(tmp_path / "run.sh"), "astra", "3"], check=True,
                   env={**os.environ, "ARM": arm, "LOG_ROOT": str(root)})
    args = json.loads((tmp_path / "arguments.json").read_text())
    assert args[args.index("--image") + 1] == image
    assert args[args.index("--epochs") + 1] == "3"
    assert args[args.index("--log-dir") + 1] == str(root / "astra")
    assert (root / "launches.txt").is_file()
    assert not (tmp_path / "logs").exists()
