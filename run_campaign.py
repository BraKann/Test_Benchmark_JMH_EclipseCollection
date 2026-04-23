#!/usr/bin/env python3
"""
Campagne JMH – Eclipse Collections × Grid5000
==============================================
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
GRADLEW = REPO_ROOT / "gradlew"

# Sécurité : vérifie que gradlew existe
if not GRADLEW.exists():
    raise FileNotFoundError(f"gradlew introuvable à {GRADLEW}")

DEFAULT_METRICS = [
    "wattmetre_power_watt",
    "bmc_power_watt",
    "rapl_package_energy_joule",
    "rapl_dram_energy_joule",
    "rapl_uncore_energy_joule",
]

DEFAULT_VERSIONS = [
    "7.0.0", "7.2.0", "8.0.0", "9.0.0", "9.1.0",
    "10.0.0", "11.0.0", "11.1.0", "12.0.0", "13.0.0",
]

# ---------------------------------------------------------------------------
# Utils temps
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def short_hostname() -> str:
    return socket.gethostname().split(".")[0]


# ---------------------------------------------------------------------------
# Build Gradle
# ---------------------------------------------------------------------------

def build_gradle_command(gradle_cmd: str, version: str) -> list[str]:
    return [
        gradle_cmd,
        "--no-daemon",
        "--rerun-tasks",
        "jmhJar",
        f"-PecVersion={version}",
    ]


# ---------------------------------------------------------------------------
# Process runner FIX IMPORTANT
# ---------------------------------------------------------------------------

def run_process(command: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # IMPORTANT FIX: sécurité chemins absolus
    command = [str(c) for c in command]

    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd.resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False
        )

        assert proc.stdout
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)

        return proc.wait()


# ---------------------------------------------------------------------------
# Warmup phase (inchangé sauf GRADLEW utilisé)
# ---------------------------------------------------------------------------

def run_warmup_phase(args: argparse.Namespace, campaign_dir: Path) -> None:
    warmup_version = args.versions[-1]

    print(f"\n{'='*60}")
    print(f"  WARMUP {warmup_version}")
    print(f"{'='*60}\n")

    warmup_dir = campaign_dir / "warmup"
    warmup_dir.mkdir(parents=True, exist_ok=True)

    gradle_cmd = build_gradle_command(str(GRADLEW), warmup_version)

    rc = run_process(gradle_cmd, REPO_ROOT, warmup_dir / "gradle-build.log")
    if rc != 0:
        print("[WARN] build warmup failed")
        return

    print("[WARMUP OK]")


# ---------------------------------------------------------------------------
# JMH command
# ---------------------------------------------------------------------------

def build_jmh_command(args: argparse.Namespace, run_dir: Path) -> list[str]:
    jar = REPO_ROOT / "build" / "libs" / "jmh-eclipse-benchmark-jmh.jar"

    return [
        args.java_command,
        "-jar", str(jar),
        args.includes or ".*",
        "-rf", args.jmh_result_format,
        "-rff", str(run_dir / "jmh-results.json"),
        "-o", str(run_dir / "jmh-human.txt"),
        "-i", str(args.iterations),
        "-wi", str(args.warmup_iterations),
        "-f", str(args.forks),
        "-r", args.iteration_time,
        "-w", args.warmup_time,
        "-tu", args.time_unit,
        "-bm", args.benchmark_mode,
    ]


# ---------------------------------------------------------------------------
# Iteration runner
# ---------------------------------------------------------------------------

def run_one_iteration(args, version, i, iter_dir, manifest):
    iter_dir.mkdir(parents=True, exist_ok=True)

    jmh_cmd = build_jmh_command(args, iter_dir)

    started = utc_now()

    rc = run_process(jmh_cmd, REPO_ROOT, iter_dir / "jmh.log")

    ended = utc_now()

    return {
        "version": version,
        "iteration": i,
        "exit_code": rc,
        "started_at": isoformat_utc(started),
        "ended_at": isoformat_utc(ended),
        "duration_seconds": (ended - started).total_seconds(),
    }


# ---------------------------------------------------------------------------
# Campaign phase (simplifié inchangé logique)
# ---------------------------------------------------------------------------

def run_campaign_phase(args, campaign_dir, manifest):
    for version in args.versions:
        print(f"\n=== VERSION {version} ===")

        version_dir = campaign_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)

        gradle_cmd = build_gradle_command(str(GRADLEW), version)
        rc = run_process(gradle_cmd, REPO_ROOT, version_dir / "gradle.log")

        if rc != 0:
            print(f"[ERROR] build failed {version}")
            continue

        for i in range(args.campaign_repeats):
            iter_dir = version_dir / f"iter_{i}"

            rec = run_one_iteration(args, version, i, iter_dir, manifest)
            manifest["runs"] = manifest.get("runs", [])
            manifest["runs"].append(rec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser()

    p.add_argument("--versions", nargs="+", default=DEFAULT_VERSIONS)
    p.add_argument("--site", required=True)
    p.add_argument("--campaign-repeats", type=int, default=3)

    p.add_argument("--iterations", type=int, default=5)
    p.add_argument("--warmup-iterations", type=int, default=5)
    p.add_argument("--forks", type=int, default=2)

    p.add_argument("--iteration-time", default="1s")
    p.add_argument("--warmup-time", default="1s")
    p.add_argument("--time-unit", default="ms")
    p.add_argument("--benchmark-mode", default="avgt")

    p.add_argument("--includes", default=None)
    p.add_argument("--java-command", default="java")

    return p.parse_args()


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    campaign_dir = REPO_ROOT / "build" / "campaign"
    campaign_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "repo_root": str(REPO_ROOT),
        "versions": args.versions,
        "site": args.site,
        "runs": []
    }

    print(f"Campaign dir: {campaign_dir}")
    print(f"Repo root: {REPO_ROOT}")

    run_warmup_phase(args, campaign_dir)
    run_campaign_phase(args, campaign_dir, manifest)

    (campaign_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())