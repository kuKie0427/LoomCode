#!/usr/bin/env python3
"""SWE-bench Lite runner for LoomCode.

Usage:
    # Run a single instance
    python scripts/swebench_runner.py single --instance django__django-11099

    # Run a batch
    python scripts/swebench_runner.py batch --slice 0:5 --predictions preds.jsonl

    # Evaluate
    python -m swebench.harness.run_evaluation \
        --dataset_name princeton-nlp/SWE-bench_Lite \
        --predictions_path preds.jsonl \
        --max_workers 4 \
        --run_id loomcode-lite
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

WORK_BASE = Path("/tmp/sweb-loom")


def clone_repo(repo, base_commit):
    repo_name = repo.split("/")[-1]
    dest = WORK_BASE / repo_name
    if dest.exists():
        subprocess.run(["git", "fetch", "--all", "--tags"], cwd=dest, capture_output=True, timeout=120)
    else:
        url = f"https://github.com/{repo}.git"
        print(f"  [repo] Cloning {url} @ {base_commit[:12]} (shallow)...")
        subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], capture_output=True, check=True, timeout=120)
        # Fetch the exact commit we need (SWE-bench uses historic commits)
        subprocess.run(["git", "fetch", "--depth", "1", "origin", base_commit], cwd=dest, capture_output=True, check=True, timeout=60)
    subprocess.run(["git", "checkout", "-f", base_commit], cwd=dest, capture_output=True, check=True, timeout=30)
    subprocess.run(["git", "clean", "-fd"], cwd=dest, capture_output=True, timeout=30)
    return dest


def get_patch(repo_dir):
    result = subprocess.run(["git", "diff"], cwd=repo_dir, capture_output=True, text=True)
    return result.stdout.strip()


def run_one(instance, model="deepseek/deepseek-v4-flash", timeout_secs=600):
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    problem = instance["problem_statement"]

    print(f"\n{'='*60}")
    print(f"Instance: {instance_id}")
    print(f"Repo: {repo} @ {base_commit[:12]}")
    print(f"{'='*60}")

    try:
        repo_dir = clone_repo(repo, base_commit)
    except Exception as e:
        print(f"  [ERROR] Clone failed: {e}")
        return None

    worker = Path(__file__).parent / "swebench_worker.py"
    log_file = Path(f"/tmp/sweb-loom/logs/{instance_id}.log")

    env = os.environ.copy()
    env["MODEL"] = model

    print(f"  [run] Starting agent_loop with model={model}...")
    print(f"  [run] Log: {log_file}")
    start = time.time()

    try:
        proc = subprocess.run(
            [sys.executable, str(worker), str(repo_dir), problem, instance_id],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
        elapsed = time.time() - start
        print(f"  [run] Completed in {elapsed:.0f}s (exit={proc.returncode})")
        elapsed = time.time() - start
        print(f"  [run] Completed in {elapsed:.0f}s (exit={proc.returncode})")
        if log_file.exists():
            tail = "\n".join(log_file.read_text().splitlines()[-10:])
            print(f"  [log tail]:\n{tail}")
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  [run] TIMEOUT after {elapsed:.0f}s")
        if log_file.exists():
            tail = "\n".join(log_file.read_text().splitlines()[-20:])
            print(f"  [log tail]:\n{tail}")

    patch = get_patch(repo_dir)
    if not patch:
        print("  [result] No changes detected")
        return ""
    print(f"  [result] Patch: {len(patch)} chars, {patch.count('diff --git')} files changed")
    return patch


def main():
    parser = argparse.ArgumentParser(description="SWE-bench Lite runner for LoomCode")
    subparsers = parser.add_subparsers(dest="command", required=True)

    single = subparsers.add_parser("single")
    single.add_argument("--instance", required=True)
    single.add_argument("--model", default="deepseek/deepseek-v4-flash")
    single.add_argument("--timeout", type=int, default=600)

    batch = subparsers.add_parser("batch")
    batch.add_argument("--slice", default="0:5")
    batch.add_argument("--predictions", default="preds.jsonl")
    batch.add_argument("--model", default="deepseek/deepseek-v4-flash")
    batch.add_argument("--timeout", type=int, default=600)

    args = parser.parse_args()
    WORK_BASE.mkdir(parents=True, exist_ok=True)

    if args.command == "single":
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        for inst in ds:
            if inst["instance_id"] == args.instance:
                break
        else:
            print(f"Instance {args.instance} not found")
            sys.exit(1)
        patch = run_one(inst, args.model, args.timeout)
        if patch is not None:
            print("\n--- PATCH ---")
            print(patch if patch else "(empty)")
        else:
            sys.exit(1)

    elif args.command == "batch":
        from datasets import load_dataset
        ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
        parts = args.slice.split(":")
        start, end = int(parts[0]), int(parts[1]) if len(parts) > 1 else len(ds)
        instances = list(ds)[start:end]

        results = []
        for inst in instances:
            patch = run_one(inst, args.model, args.timeout)
            results.append({
                "instance_id": inst["instance_id"],
                "model_patch": patch or "",
            })
            time.sleep(2)

        pred_path = Path(args.predictions)
        with pred_path.open("w") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

        total = len(results)
        non_empty = sum(1 for r in results if r["model_patch"])
        print(f"\n{'='*60}")
        print(f"Results written to {pred_path}")
        print(f"Total: {total}, Non-empty patches: {non_empty}/{total}")


if __name__ == "__main__":
    main()
