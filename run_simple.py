#!/usr/bin/env python3
"""
Campagne JMH simplifiée — debug Kwollect
=========================================
- 2 versions seulement (11.1.0 et 12.0.0 par défaut)
- Pas de warmup machine
- 2 itérations par version
- Affichage détaillé de chaque étape Kwollect pour déboguer
- Pauses courtes

Utilisation :
  python3 run_simple.py --site lyon
  python3 run_simple.py --site lyon --skip-kwollect   # sans Kwollect
  python3 run_simple.py --site lyon --versions 11.1.0 13.0.0
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
REPO_ROOT = Path(__file__).resolve().parent
# ---------------------------------------------------------------------------


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

def fetch_kwollect(
        site: str,
        node: str,
        start_epoch: int,
        end_epoch: int,
        metrics: list[str],
        job_id: str | None,
) -> list[dict[str, Any]]:
    """
    Appel direct à l'API Kwollect de Grid5000.
    Affiche l'URL complète pour faciliter le débogage.
    """
    query: dict[str, str] = {
        "nodes": node,
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
            print(f"  [Kwollect] {len(data)} enregistrement(s) reçu(s).")
            return data
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"  [Kwollect ERREUR HTTP {exc.code}] {exc.reason}")
        print(f"  [Kwollect] Corps : {body[:400]}")
        raise
    except urllib.error.URLError as exc:
        print(f"  [Kwollect ERREUR réseau] {exc.reason}")
        raise


def summarise_kwollect(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Retourne moyenne, pic et énergie intégrée (méthode trapèzes)."""
    if not records:
        return {"samples": 0, "average_power_w": None,
                "peak_power_w": None, "energy_j": None}

    samples = sorted(
        [(float(r["timestamp"]) if isinstance(r["timestamp"], (int, float))
          else datetime.fromisoformat(
            r["timestamp"].replace("Z", "+00:00")).timestamp(),
          float(r["value"]))
         for r in records if r.get("value") is not None],
        key=lambda t: t[0],
    )
    if not samples:
        return {"samples": 0, "average_power_w": None,
                "peak_power_w": None, "energy_j": None}

    peak = max(v for _, v in samples)
    if len(samples) == 1:
        return {"samples": 1, "average_power_w": samples[0][1],
                "peak_power_w": peak, "energy_j": 0.0}

    area = sum(((p0 + p1) / 2.0) * max(0.0, t1 - t0)
               for (t0, p0), (t1, p1) in zip(samples, samples[1:]))
    duration = max(0.0, samples[-1][0] - samples[0][0])
    avg = area / duration if duration > 0 else samples[0][1]

    return {
        "samples": len(samples),
        "duration_seconds": duration,
        "average_power_w": avg,
        "peak_power_w": peak,
        "energy_j": area,
    }


# ---------------------------------------------------------------------------
# Processus
# ---------------------------------------------------------------------------

def run_process(command: list[str], cwd: Path, log_path: Path) -> int:
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

def run_version(
        args: argparse.Namespace,
        version: str,
        out_dir: Path,
) -> dict[str, Any]:
    """
    Pour une version donnée :
      1. Build du jar JMH avec la bonne version EC
      2. N itérations JMH + collecte Kwollect
    """
    version_dir = out_dir / slugify(version)
    version_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  VERSION {version}")
    print(f"{'='*60}")

    # ----- Build -----------------------------------------------------------
    gradle_cmd = [
        args.gradle_command,
        "--no-daemon", "--rerun-tasks",
        "jmhJar",
        f"-PecVersion={version}",
    ]
    build_rc = run_process(gradle_cmd, REPO_ROOT, version_dir / "build.log")
    if build_rc != 0:
        print(f"  [ERREUR] Build échoué (code {build_rc})")
        return {"version": version, "build_failed": True, "iterations": []}

    jar = REPO_ROOT / "build" / "libs" / "jmh-eclipse-benchmark-jmh.jar"
    if not jar.exists():
        print(f"  [ERREUR] Jar introuvable : {jar}")
        return {"version": version, "jar_missing": True, "iterations": []}

    # ----- Itérations JMH -------------------------------------------------
    iterations_data = []

    for it in range(1, args.repeats + 1):
        print(f"\n  ── Itération {it}/{args.repeats} ──")
        iter_dir = version_dir / f"iter_{it:02d}"
        iter_dir.mkdir(parents=True, exist_ok=True)

        jmh_cmd = [
            args.java_command, "-jar", str(jar),
            ".*",
            "-rf", "JSON",
            "-rff", str(iter_dir / "jmh-results.json"),
            "-o",  str(iter_dir / "jmh-human.txt"),
            "-i",  str(args.iterations),
            "-wi", str(args.warmup_iterations),
            "-f",  str(args.forks),
            "-r",  "1s",
            "-w",  "1s",
            "-tu", "ms",
            "-bm", "avgt",
        ]

        started  = utc_now()
        jmh_rc   = run_process(jmh_cmd, REPO_ROOT, iter_dir / "jmh-run.log")
        ended    = utc_now()
        duration = (ended - started).total_seconds()

        print(f"  [JMH] exit={jmh_rc}  durée={duration:.1f}s")
        print(f"  [Kwollect] Pause {args.rest_seconds}s avant fetch …")
        time.sleep(args.rest_seconds)

        iter_record: dict[str, Any] = {
            "version":          version,
            "iteration":        it,
            "started_at":       isoformat_utc(started),
            "ended_at":         isoformat_utc(ended),
            "duration_seconds": duration,
            "jmh_exit_code":    jmh_rc,
        }

        # Kwollect
        if not args.skip_kwollect:
            start_epoch = math.floor(started.timestamp())
            end_epoch   = math.ceil(ended.timestamp())
            print(f"  [Kwollect] Fenêtre : {start_epoch} → {end_epoch} "
                  f"({end_epoch - start_epoch}s)")

            for metric in args.metrics:
                print(f"  [Kwollect] Métrique : {metric}")
                try:
                    records = fetch_kwollect(
                        site=args.site, node=args.node,
                        start_epoch=start_epoch, end_epoch=end_epoch,
                        metrics=[metric], job_id=args.job_id,
                    )
                    # Sauvegarde brute
                    raw_path = iter_dir / f"kwollect_{metric}.json"
                    raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")

                    summary = summarise_kwollect(records)
                    iter_record[f"kwollect_{metric}"] = summary
                    print(f"  [Kwollect] → {summary}")

                except Exception as exc:
                    iter_record[f"kwollect_{metric}_error"] = str(exc)
                    print(f"  [Kwollect] ÉCHEC pour {metric} : {exc}")
        else:
            print("  [Kwollect] ignoré (--skip-kwollect)")

        # Sauvegarde itération
        (iter_dir / "summary.json").write_text(
            json.dumps(iter_record, indent=2), encoding="utf-8"
        )
        iterations_data.append(iter_record)

        # Pause inter-itérations
        if it < args.repeats:
            print(f"  [pause inter-itération {args.inter_rest}s …]")
            time.sleep(args.inter_rest)

    return {"version": version, "iterations": iterations_data}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Campagne JMH simplifiée — 2 versions, debug Kwollect",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--versions", nargs="+",
                        default=["11.1.0", "12.0.0"],
                        help="Versions Eclipse Collections à tester.")
    parser.add_argument("--site", required=True,
                        help="Site Grid5000 (ex: lyon, grenoble …).")
    parser.add_argument("--node", default=short_hostname(),
                        help="Nom du nœud réservé (défaut : hostname courant).")
    parser.add_argument("--job-id", default=os.environ.get("OAR_JOB_ID"),
                        help="ID job OAR (lu depuis $OAR_JOB_ID si absent).")
    parser.add_argument("--metrics", nargs="+",
                        default=["wattmetre_power_watt"],
                        help="Métriques Kwollect.")
    parser.add_argument("--repeats",           type=int, default=2,
                        help="Nombre d'itérations par version.")
    parser.add_argument("--iterations",        type=int, default=3,
                        help="Itérations de mesure JMH.")
    parser.add_argument("--warmup-iterations", type=int, default=3,
                        help="Itérations de warmup JMH.")
    parser.add_argument("--forks",             type=int, default=1,
                        help="Forks JMH.")
    parser.add_argument("--rest-seconds",  type=int, default=10,
                        help="Pause après chaque run JMH avant fetch Kwollect.")
    parser.add_argument("--inter-rest",    type=int, default=15,
                        help="Pause entre deux itérations.")
    parser.add_argument("--skip-kwollect", action="store_true",
                        help="Désactiver Kwollect.")
    parser.add_argument("--output-dir", default=None,
                        help="Dossier de sortie (défaut : build/campaigns/<ts>).")
    parser.add_argument("--gradle-command",
                        default="gradlew.bat" if os.name == "nt" else "./gradlew")
    parser.add_argument("--java-command", default="java")

    args = parser.parse_args()

    # Infos de départ
    print(f"\n  Nœud     : {args.node}")
    print(f"  Site     : {args.site}")
    print(f"  Job ID   : {args.job_id or '(non défini)'}")
    print(f"  Versions : {args.versions}")
    print(f"  Métriques: {args.metrics}")
    print(f"  Répétitions / version : {args.repeats}")
    if args.skip_kwollect:
        print("  ⚠  Kwollect DÉSACTIVÉ\n")

    ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(args.output_dir) if args.output_dir else (
            REPO_ROOT / "build" / "campaigns" / ts
    )
    out.mkdir(parents=True, exist_ok=True)
    print(f"  Sortie   : {out}\n")

    all_results = []
    for version in args.versions:
        result = run_version(args, version, out)
        all_results.append(result)

    # Résumé global
    summary_path = out / "campaign-summary.json"
    summary_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\n  ✓ Résumé global : {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())