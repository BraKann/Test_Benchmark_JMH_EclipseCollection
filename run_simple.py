#!/usr/bin/env python3
"""
Campagne JMH simplifiee - Grid5000

Commande de reference :
  python3 scripts/run_simple.py \
      --versions 11.1.0 12.0.0 \
      --site lyon \
      --node "$(hostname -s)" \
      --job-id "$OAR_JOB_ID" \
      --metrics wattmetre_power_watt \
      --includes 'benchmark.(List|Map|Set|Bag).*' \
      --iterations 5 \
      --warmup-iterations 5 \
      --forks 2 \
      --iteration-time 1s \
      --warmup-time 1s \
      --idle-seconds 30 \
      --rest-seconds 10

Test local (sans Kwollect) :
  python3 scripts/run_simple.py \
      --versions 11.1.0 12.0.0 \
      --site lyon \
      --skip-kwollect \
      --includes 'benchmark.(List|Map|Set|Bag).*' \
      --iterations 3 --warmup-iterations 3 --forks 1
"""

from __future__ import annotations

import argparse
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

# REPO_ROOT = dossier parent de scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in value)


def short_hostname() -> str:
    return socket.gethostname().split(".")[0]


# ---------------------------------------------------------------------------
# Kwollect
# ---------------------------------------------------------------------------

def fetch_kwollect(site, node, start_epoch, end_epoch, metrics, job_id):
    query = {
        "nodes":      node,
        "start_time": str(start_epoch),
        "end_time":   str(end_epoch),
        "metrics":    ",".join(metrics),
    }
    if job_id:
        query["job_id"] = job_id

    url = (
        f"https://api.grid5000.fr/stable/sites/{site}/metrics?"
        f"{urllib.parse.urlencode(query)}"
    )
    print(f"  [Kwollect] URL : {url}")

    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
            print(f"  [Kwollect] {len(data)} enregistrement(s) recu(s).")
            return data
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"  [Kwollect ERREUR HTTP {exc.code}] {exc.reason}")
        print(f"  [Kwollect] Reponse : {body[:500]}")
        raise
    except urllib.error.URLError as exc:
        print(f"  [Kwollect ERREUR reseau] {exc.reason}")
        raise


def summarise_kwollect(records):
    if not records:
        return {"samples": 0, "average_power_w": None, "peak_power_w": None, "energy_j": None}

    def to_epoch(ts):
        if isinstance(ts, (int, float)):
            return float(ts)
        s = str(ts).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()

    samples = sorted(
        [(to_epoch(r["timestamp"]), float(r["value"]))
         for r in records if r.get("value") is not None],
        key=lambda t: t[0],
    )
    if not samples:
        return {"samples": 0, "average_power_w": None, "peak_power_w": None, "energy_j": None}

    peak = max(v for _, v in samples)
    if len(samples) == 1:
        return {"samples": 1, "average_power_w": samples[0][1], "peak_power_w": peak, "energy_j": 0.0}

    area = sum(
        ((p0 + p1) / 2.0) * max(0.0, t1 - t0)
        for (t0, p0), (t1, p1) in zip(samples, samples[1:])
    )
    duration = max(0.0, samples[-1][0] - samples[0][0])
    avg = area / duration if duration > 0 else samples[0][1]

    return {
        "samples":          len(samples),
        "duration_seconds": duration,
        "average_power_w":  avg,
        "peak_power_w":     peak,
        "energy_j":         area,
    }


# ---------------------------------------------------------------------------
# Processus
# ---------------------------------------------------------------------------

def run_process(command, cwd, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"  [CMD] {' '.join(command)}")
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command, cwd=str(cwd),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        assert proc.stdout
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        return proc.wait()


# ---------------------------------------------------------------------------
# Campagne
# ---------------------------------------------------------------------------

def run_version(args, version, out_dir):
    version_dir = out_dir / slugify(version)
    version_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  VERSION {version}")
    print(f"{'='*60}")

    # Build
    gradle_cmd = [
        args.gradle_command,
        "--no-daemon", "--rerun-tasks",
        "jmhJar",
        f"-PecVersion={version}",
    ]
    build_rc = run_process(gradle_cmd, REPO_ROOT, version_dir / "build.log")
    if build_rc != 0:
        print(f"  [ERREUR] Build echoue (code {build_rc})")
        return {"version": version, "build_failed": True, "iterations": []}

    jar = REPO_ROOT / "build" / "libs" / "jmh-eclipse-benchmark-jmh.jar"
    if not jar.exists():
        print(f"  [ERREUR] Jar introuvable : {jar}")
        return {"version": version, "jar_missing": True, "iterations": []}

    iterations_data = []

    for it in range(1, args.repeats + 1):
        print(f"\n  -- Iteration {it}/{args.repeats} --")
        iter_dir = version_dir / f"iter_{it:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        # Idle window
        idle_start = idle_end = None
        if args.idle_seconds > 0:
            print(f"  [idle {args.idle_seconds}s - mesure puissance de base ...]")
            idle_start = utc_now()
            time.sleep(args.idle_seconds)
            idle_end = utc_now()

        # Run JMH
        jmh_cmd = [
            args.java_command, "-jar", str(jar),
            args.includes,
            "-rf",  "JSON",
            "-rff", str(iter_dir / "jmh-results.json"),
            "-o",   str(iter_dir / "jmh-human.txt"),
            "-i",   str(args.iterations),
            "-wi",  str(args.warmup_iterations),
            "-f",   str(args.forks),
            "-r",   args.iteration_time,
            "-w",   args.warmup_time,
            "-tu",  "ms",
            "-bm",  "avgt",
        ]

        started  = utc_now()
        jmh_rc   = run_process(jmh_cmd, REPO_ROOT, iter_dir / "jmh-run.log")
        ended    = utc_now()
        duration = (ended - started).total_seconds()
        print(f"  [JMH] exit={jmh_rc}  duree={duration:.1f}s")

        iter_record = {
            "version":          version,
            "iteration":        it,
            "started_at":       isoformat_utc(started),
            "ended_at":         isoformat_utc(ended),
            "duration_seconds": duration,
            "jmh_exit_code":    jmh_rc,
        }
        if idle_start:
            iter_record["idle_window"] = {
                "start": isoformat_utc(idle_start),
                "end":   isoformat_utc(idle_end),
            }

        # Kwollect
        if not args.skip_kwollect:
            print(f"  [Kwollect] Pause {args.rest_seconds}s avant fetch ...")
            time.sleep(args.rest_seconds)

            start_epoch = math.floor(started.timestamp())
            end_epoch   = math.ceil(ended.timestamp())
            print(f"  [Kwollect] Fenetre run  : {start_epoch} -> {end_epoch} ({end_epoch - start_epoch}s)")

            for metric in args.metrics:
                print(f"  [Kwollect] Metrique : {metric}")
                try:
                    records = fetch_kwollect(
                        site=args.site, node=args.node,
                        start_epoch=start_epoch, end_epoch=end_epoch,
                        metrics=[metric], job_id=args.job_id,
                    )
                    (iter_dir / f"kwollect_bench_{metric}.json").write_text(
                        json.dumps(records, indent=2), encoding="utf-8"
                    )
                    bench_sum = summarise_kwollect(records)
                    iter_record[f"kwollect_{metric}"] = bench_sum
                    print(f"  [Kwollect] benchmark  -> {bench_sum}")

                    # Idle Kwollect
                    if idle_start:
                        idle_s = math.floor(idle_start.timestamp())
                        idle_e = math.ceil(idle_end.timestamp())
                        print(f"  [Kwollect] Fenetre idle : {idle_s} -> {idle_e}")
                        try:
                            idle_rec = fetch_kwollect(
                                site=args.site, node=args.node,
                                start_epoch=idle_s, end_epoch=idle_e,
                                metrics=[metric], job_id=args.job_id,
                            )
                            (iter_dir / f"kwollect_idle_{metric}.json").write_text(
                                json.dumps(idle_rec, indent=2), encoding="utf-8"
                            )
                            idle_sum = summarise_kwollect(idle_rec)
                            iter_record[f"kwollect_idle_{metric}"] = idle_sum
                            print(f"  [Kwollect] idle        -> {idle_sum}")

                            run_e  = bench_sum.get("energy_j")
                            idle_p = idle_sum.get("average_power_w")
                            if run_e is not None and idle_p is not None:
                                net = run_e - idle_p * duration
                                iter_record[f"net_energy_j_{metric}"] = net
                                print(f"  [Kwollect] energie nette = {net:.2f} J")
                        except Exception as exc:
                            print(f"  [Kwollect idle] ECHEC : {exc}")

                except Exception as exc:
                    iter_record[f"kwollect_{metric}_error"] = str(exc)
                    print(f"  [Kwollect] ECHEC pour {metric} : {exc}")
        else:
            print("  [Kwollect] ignore (--skip-kwollect)")

        (iter_dir / "summary.json").write_text(
            json.dumps(iter_record, indent=2), encoding="utf-8"
        )
        iterations_data.append(iter_record)

        if it < args.repeats and args.inter_rest > 0:
            print(f"  [inter-iteration {args.inter_rest}s ...]")
            time.sleep(args.inter_rest)

    return {"version": version, "iterations": iterations_data}


# ---------------------------------------------------------------------------
# Arguments CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Campagne JMH simplifiee - Grid5000",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--versions", nargs="+", default=["11.1.0", "12.0.0"],
                        help="Versions Eclipse Collections a tester.")

    # Grid5000
    parser.add_argument("--site", required=True,
                        help="Site Grid5000 (ex: lyon).")
    parser.add_argument("--node", default=short_hostname(),
                        help="Nom du noeud (defaut : hostname courant).")
    parser.add_argument("--job-id", default=os.environ.get("OAR_JOB_ID"),
                        help="ID job OAR.")
    parser.add_argument("--metrics", nargs="+", default=["wattmetre_power_watt"],
                        help="Metriques Kwollect.")

    # JMH
    parser.add_argument("--includes", default=".*",
                        help="Regex JMH pour filtrer les benchmarks.")
    parser.add_argument("--iterations",        type=int, default=5,
                        help="Iterations de mesure JMH.")
    parser.add_argument("--warmup-iterations", type=int, default=5,
                        help="Iterations de warmup JMH.")
    parser.add_argument("--forks",             type=int, default=2,
                        help="Nombre de forks JMH.")
    parser.add_argument("--iteration-time",  default="1s",
                        help="Duree d'une iteration JMH.")
    parser.add_argument("--warmup-time",     default="1s",
                        help="Duree d'une iteration de warmup JMH.")

    # Temporisation
    parser.add_argument("--idle-seconds",  type=int, default=30,
                        help="Duree fenetre idle avant chaque run.")
    parser.add_argument("--rest-seconds",  type=int, default=10,
                        help="Pause apres chaque run avant fetch Kwollect.")
    parser.add_argument("--inter-rest",    type=int, default=15,
                        help="Pause entre deux iterations d'une meme version.")
    parser.add_argument("--repeats",       type=int, default=2,
                        help="Nombre d'iterations de campagne par version.")

    # Flags
    parser.add_argument("--skip-kwollect", action="store_true",
                        help="Desactiver Kwollect (test local).")
    parser.add_argument("--output-dir", default=None,
                        help="Dossier de sortie.")

    # Systeme
    parser.add_argument("--gradle-command",
                        default="gradlew.bat" if os.name == "nt" else "./gradlew")
    parser.add_argument("--java-command", default="java")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    print(f"\n{'='*60}")
    print(f"  Noeud         : {args.node}")
    print(f"  Site          : {args.site}")
    print(f"  Job ID        : {args.job_id or '(non defini)'}")
    print(f"  Versions      : {args.versions}")
    print(f"  Metriques     : {args.metrics}")
    print(f"  Includes      : {args.includes}")
    print(f"  Iterations    : {args.iterations} mesure / {args.warmup_iterations} warmup")
    print(f"  Forks         : {args.forks}")
    print(f"  Temps iter    : {args.iteration_time} / warmup : {args.warmup_time}")
    print(f"  Idle          : {args.idle_seconds}s  |  Rest : {args.rest_seconds}s")
    print(f"  Repetitions   : {args.repeats} par version")
    if args.skip_kwollect:
        print("  KWOLLECT DESACTIVE")
    print(f"{'='*60}\n")

    ts  = utc_now().strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.output_dir) if args.output_dir else (
            REPO_ROOT / "build" / "campaigns" / ts
    )
    out.mkdir(parents=True, exist_ok=True)
    print(f"  Sortie : {out}\n")

    all_results = []
    for version in args.versions:
        result = run_version(args, version, out)
        all_results.append(result)

    summary_path = out / "campaign-summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\n  OK - Resume global : {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())