#!/usr/bin/env python3
"""
Campagne JMH – Eclipse Collections × Grid5000
==============================================

Protocole :
  1. Warmup machine  — un run unique (version la plus récente) sans collecte.
  2. Campagne principale — pour chaque version, N itérations avec pause entre chaque.
  3. Fetch Kwollect   — métriques énergie/puissance après chaque itération.
  4. Analyse          — moyenne, écart-type, histogrammes (total + CPU/DRAM/cache RAPL si dispo).

Utilisation typique sur Grid5000 :
  python3 run_campaign.py \\
      --versions 7.0.0 7.2.0 8.0.0 9.0.0 9.1.0 10.0.0 11.0.0 11.1.0 12.0.0 13.0.0 \\
      --site lyon \\
      --campaign-repeats 5 \\
      --idle-seconds 30 \\
      --rest-seconds 15

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

# Métriques Kwollect demandées par défaut
DEFAULT_METRICS = [
    "wattmetre_power_watt",   # puissance totale nœud (wattmètre)
    "bmc_power_watt",         # puissance BMC
    # RAPL – disponibles sur certains nœuds Grid5000
    "rapl_package_energy_joule",
    "rapl_dram_energy_joule",
    "rapl_uncore_energy_joule",
]

# Versions cibles par défaut
DEFAULT_VERSIONS = [
    "7.0.0", "7.2.0", "8.0.0", "9.0.0", "9.1.0",
    "10.0.0", "11.0.0", "11.1.0", "12.0.0", "13.0.0",
]

# ---------------------------------------------------------------------------
# Utilitaires temporels
# ---------------------------------------------------------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    candidate = str(value).strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    return datetime.fromisoformat(candidate).timestamp()


def slugify(value: str) -> str:
    return "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in value)


def short_hostname() -> str:
    return socket.gethostname().split(".")[0]


# ---------------------------------------------------------------------------
# Statistiques énergie / puissance
# ---------------------------------------------------------------------------

def compute_power_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcule moyenne, pic, énergie intégrée par trapèzes depuis une série Kwollect."""
    if not records:
        return {"samples": 0, "duration_seconds": 0.0,
                "average_power_w": None, "peak_power_w": None, "energy_j": None}

    ordered = sorted(records, key=lambda r: parse_timestamp(r["timestamp"]))
    samples = [
        (parse_timestamp(r["timestamp"]), float(r["value"]))
        for r in ordered if r.get("value") is not None
    ]
    if not samples:
        return {"samples": 0, "duration_seconds": 0.0,
                "average_power_w": None, "peak_power_w": None, "energy_j": None}

    peak = max(v for _, v in samples)
    if len(samples) == 1:
        return {"samples": 1, "duration_seconds": 0.0,
                "average_power_w": samples[0][1], "peak_power_w": peak, "energy_j": 0.0}

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


def compute_rapl_energy_delta(records: list[dict[str, Any]]) -> float | None:
    """
    Pour les métriques RAPL (compteurs d'énergie cumulatifs),
    retourne la différence max – min (en joules) sur la fenêtre.
    """
    values = [float(r["value"]) for r in records if r.get("value") is not None]
    if len(values) < 2:
        return None
    return max(values) - min(values)


# ---------------------------------------------------------------------------
# Persistance
# ---------------------------------------------------------------------------

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["timestamp", "device_id", "metric_id", "value", "labels"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "timestamp": row.get("timestamp"),
                "device_id": row.get("device_id"),
                "metric_id": row.get("metric_id"),
                "value": row.get("value"),
                "labels": json.dumps(row.get("labels", {}), sort_keys=True),
            })


# ---------------------------------------------------------------------------
# Kwollect
# ---------------------------------------------------------------------------

def fetch_kwollect_metrics(
    *,
    site: str,
    node: str,
    start_epoch: int,
    end_epoch: int,
    metrics: list[str],
    job_id: str | None,
) -> list[dict[str, Any]]:
    query: dict[str, str] = {
        "nodes": node,
        "start_time": str(start_epoch),
        "end_time": str(end_epoch),
        "metrics": ",".join(metrics),
    }
    if job_id:
        query["job_id"] = job_id

    url = (
        f"https://api.grid5000.fr/stable/sites/{site}/metrics?"
        f"{urllib.parse.urlencode(query)}"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def group_by_metric(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Regroupe les enregistrements Kwollect par metric_id."""
    groups: dict[str, list] = {}
    for r in records:
        mid = r.get("metric_id", "unknown")
        groups.setdefault(mid, []).append(r)
    return groups


# ---------------------------------------------------------------------------
# Build & JMH
# ---------------------------------------------------------------------------

def build_gradle_command(gradle_cmd: str, version: str) -> list[str]:
    return [
        gradle_cmd,
        "--no-daemon",
        "--rerun-tasks",
        "jmhJar",
        f"-PecVersion={version}",
    ]


def build_jmh_command(args: argparse.Namespace, run_dir: Path) -> list[str]:
    ext = args.jmh_result_format.lower()
    jar = REPO_ROOT / "build" / "libs" / "jmh-eclipse-benchmark-jmh.jar"

    cmd = [
        args.java_command, "-jar", str(jar),
        args.includes or ".*",
        "-rf", args.jmh_result_format,
        "-rff", str(run_dir / f"jmh-results.{ext}"),
        "-o",  str(run_dir / "jmh-human.txt"),
        "-i",  str(args.iterations),
        "-wi", str(args.warmup_iterations),
        "-f",  str(args.forks),
        "-r",  args.iteration_time,
        "-w",  args.warmup_time,
        "-tu", args.time_unit,
        "-bm", args.benchmark_mode,
    ]
    if args.excludes:
        cmd.extend(["-e", args.excludes])
    for p in args.benchmark_param:
        cmd.extend(["-p", p])
    return cmd


def run_process(command: list[str], cwd: Path, log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
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
# Phase : Warmup machine
# ---------------------------------------------------------------------------

def run_warmup_phase(args: argparse.Namespace, campaign_dir: Path) -> None:
    """
    Lance un run de chauffe sur la version la plus récente.
    Objectif : stabiliser la température CPU et le comportement JIT
    avant la campagne de mesure.
    """
    warmup_version = args.versions[-1]
    print(f"\n{'='*60}")
    print(f"  PHASE WARMUP — version {warmup_version}")
    print(f"  (run non mesuré pour chauffer la machine)")
    print(f"{'='*60}\n")

    warmup_dir = campaign_dir / "warmup"
    warmup_dir.mkdir(parents=True, exist_ok=True)

    # Build
    gradle_cmd = build_gradle_command(args.gradle_command, warmup_version)
    rc = run_process(gradle_cmd, REPO_ROOT, warmup_dir / "gradle-build.log")
    if rc != 0:
        print(f"[WARMUP] Build échoué (code {rc}) — on continue quand même.")
        return

    # JMH warmup (forks=1, itérations réduites)
    ext = args.jmh_result_format.lower()
    jar = REPO_ROOT / "build" / "libs" / "jmh-eclipse-benchmark-jmh.jar"
    jmh_warmup_cmd = [
        args.java_command, "-jar", str(jar),
        args.includes or ".*",
        "-rf", args.jmh_result_format,
        "-rff", str(warmup_dir / f"jmh-results.{ext}"),
        "-o",  str(warmup_dir / "jmh-human.txt"),
        "-i",  "3",
        "-wi", "3",
        "-f",  "1",
        "-r",  args.warmup_time,
        "-w",  args.warmup_time,
        "-tu", args.time_unit,
        "-bm", args.benchmark_mode,
    ]
    if args.excludes:
        jmh_warmup_cmd.extend(["-e", args.excludes])
    for p in args.benchmark_param:
        jmh_warmup_cmd.extend(["-p", p])

    run_process(jmh_warmup_cmd, REPO_ROOT, warmup_dir / "jmh-run.log")

    print(f"\n[WARMUP] Terminé — pause {args.rest_seconds}s avant la campagne.\n")
    time.sleep(args.rest_seconds)


# ---------------------------------------------------------------------------
# Phase : Campagne principale
# ---------------------------------------------------------------------------

def run_one_iteration(
    args: argparse.Namespace,
    version: str,
    iter_idx: int,
    iter_dir: Path,
    manifest: dict,
) -> dict[str, Any]:
    """Lance un run JMH et collecte les métriques Kwollect."""
    record: dict[str, Any] = {
        "version": version,
        "iteration": iter_idx,
        "node": args.node,
        "site": args.site,
        "job_id": args.job_id,
        "metrics": args.metrics,
    }

    # Idle window optionnelle
    if args.idle_seconds > 0:
        print(f"  [idle {args.idle_seconds}s …]")
        idle_start = utc_now()
        time.sleep(args.idle_seconds)
        idle_end = utc_now()
        record["idle_window"] = {
            "start": isoformat_utc(idle_start),
            "end": isoformat_utc(idle_end),
            "duration_seconds": args.idle_seconds,
        }

    # Lancement JMH
    jmh_cmd = build_jmh_command(args, iter_dir)
    record["jmh_command"] = jmh_cmd
    started = utc_now()
    exit_code = run_process(jmh_cmd, REPO_ROOT, iter_dir / "jmh-run.log")
    ended = utc_now()

    record.update({
        "started_at": isoformat_utc(started),
        "ended_at": isoformat_utc(ended),
        "duration_seconds": (ended - started).total_seconds(),
        "exit_code": exit_code,
    })

    # Pause post-run
    if args.rest_seconds > 0:
        print(f"  [rest {args.rest_seconds}s …]")
        time.sleep(args.rest_seconds)

    # Collecte Kwollect
    if not args.skip_kwollect:
        time.sleep(args.kwollect_settle_seconds)
        _collect_kwollect(args, record, started, ended, iter_dir)

    write_json(iter_dir / "run-summary.json", record)
    return record


def _collect_kwollect(
    args: argparse.Namespace,
    record: dict[str, Any],
    started: datetime,
    ended: datetime,
    iter_dir: Path,
) -> None:
    """Fetch et résume les métriques Kwollect pour un run."""
    try:
        raw = fetch_kwollect_metrics(
            site=args.site, node=args.node,
            start_epoch=math.floor(started.timestamp()),
            end_epoch=math.ceil(ended.timestamp()),
            metrics=args.metrics,
            job_id=args.job_id,
        )
        write_json(iter_dir / "kwollect-benchmark.json", raw)
        write_csv(iter_dir / "kwollect-benchmark.csv", raw)

        grouped = group_by_metric(raw)
        summary: dict[str, Any] = {}

        # Métriques de puissance instantanée
        for metric in ("wattmetre_power_watt", "bmc_power_watt"):
            if metric in grouped:
                summary[metric] = compute_power_summary(grouped[metric])

        # Métriques RAPL (énergie cumulée → delta)
        for metric in ("rapl_package_energy_joule",
                        "rapl_dram_energy_joule",
                        "rapl_uncore_energy_joule"):
            if metric in grouped:
                delta = compute_rapl_energy_delta(grouped[metric])
                summary[metric] = {"energy_j": delta}

        record["kwollect_benchmark_summary"] = summary

    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        record["kwollect_benchmark_error"] = str(exc)
        print(f"  [Kwollect ERREUR] {exc}")

    # Idle Kwollect
    if "idle_window" in record:
        iw = record["idle_window"]
        try:
            idle_raw = fetch_kwollect_metrics(
                site=args.site, node=args.node,
                start_epoch=math.floor(parse_timestamp(iw["start"])),
                end_epoch=math.ceil(parse_timestamp(iw["end"])),
                metrics=args.metrics,
                job_id=args.job_id,
            )
            write_json(iter_dir / "kwollect-idle.json", idle_raw)
            write_csv(iter_dir / "kwollect-idle.csv", idle_raw)
            idle_grouped = group_by_metric(idle_raw)

            idle_summary: dict[str, Any] = {}
            for metric in ("wattmetre_power_watt", "bmc_power_watt"):
                if metric in idle_grouped:
                    idle_summary[metric] = compute_power_summary(idle_grouped[metric])
            record["kwollect_idle_summary"] = idle_summary

            # Énergie nette = énergie run – (puissance idle × durée run)
            bench_s = record.get("kwollect_benchmark_summary", {})
            watt_bench = bench_s.get("wattmetre_power_watt", {})
            watt_idle  = idle_summary.get("wattmetre_power_watt", {})
            run_energy  = watt_bench.get("energy_j")
            idle_avg_pw = watt_idle.get("average_power_w")
            if run_energy is not None and idle_avg_pw is not None:
                record["net_energy_j"] = run_energy - idle_avg_pw * record["duration_seconds"]

        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            record["kwollect_idle_error"] = str(exc)


def run_campaign_phase(
    args: argparse.Namespace,
    campaign_dir: Path,
    manifest: dict,
) -> None:
    """Campagne principale : N itérations par version, avec rebuild avant chaque version."""
    for version in args.versions:
        version_slug = slugify(version)
        version_dir = campaign_dir / version_slug
        version_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*60}")
        print(f"  VERSION {version}  ({args.campaign_repeats} itération(s))")
        print(f"{'='*60}")

        # Build une seule fois par version
        gradle_cmd = build_gradle_command(args.gradle_command, version)
        build_rc = run_process(gradle_cmd, REPO_ROOT, version_dir / "gradle-build.log")
        if build_rc != 0:
            print(f"  [ERREUR] Build échoué pour {version} (code {build_rc}) — version ignorée.")
            manifest["runs"].append({
                "version": version, "build_exit_code": build_rc, "iterations": []
            })
            write_json(campaign_dir / "campaign-manifest.json", manifest)
            continue

        version_record: dict[str, Any] = {"version": version, "iterations": []}

        for it in range(1, args.campaign_repeats + 1):
            print(f"\n  --- Itération {it}/{args.campaign_repeats} ---")
            iter_dir = version_dir / f"iter_{it:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            rec = run_one_iteration(args, version, it, iter_dir, manifest)
            version_record["iterations"].append(rec)
            write_json(campaign_dir / "campaign-manifest.json", manifest)

            # Pause inter-itérations (sauf après la dernière)
            if it < args.campaign_repeats and args.inter_iteration_rest > 0:
                print(f"  [inter-itération rest {args.inter_iteration_rest}s …]")
                time.sleep(args.inter_iteration_rest)

        manifest["runs"].append(version_record)
        write_json(campaign_dir / "campaign-manifest.json", manifest)


# ---------------------------------------------------------------------------
# Analyse & visualisation
# ---------------------------------------------------------------------------

def load_campaign_results(campaign_dir: Path) -> dict[str, list[dict]]:
    """
    Charge tous les run-summary.json de la campagne.
    Retourne {version: [iter_record, …]}.
    """
    results: dict[str, list[dict]] = {}
    manifest_path = campaign_dir / "campaign-manifest.json"
    if not manifest_path.exists():
        return results

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for version_record in manifest.get("runs", []):
        version = version_record.get("version", "?")
        results[version] = version_record.get("iterations", [])
    return results


def extract_metric_values(
    iterations: list[dict],
    metric_path: tuple[str, ...],
) -> list[float]:
    """Extrait une valeur imbriquée depuis chaque itération (filtre None)."""
    values = []
    for it in iterations:
        node = it
        for key in metric_path:
            node = node.get(key, {}) if isinstance(node, dict) else None
        if isinstance(node, (int, float)) and node is not None:
            values.append(float(node))
    return values


def compute_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "std": None, "min": None, "max": None}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n if n > 1 else 0.0
    return {
        "n": n,
        "mean": mean,
        "std": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
    }


def analyse_and_plot(campaign_dir: Path, skip_plot: bool = False) -> None:
    """Génère les statistiques et les graphiques pour toute la campagne."""

    print(f"\n{'='*60}")
    print("  ANALYSE")
    print(f"{'='*60}\n")

    results = load_campaign_results(campaign_dir)
    if not results:
        print("  Aucun résultat à analyser.")
        return

    analysis_dir = campaign_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    # ---- Calcul des statistiques ----------------------------------------

    summary_rows = []
    full_stats: dict[str, Any] = {}

    metric_paths = {
        "energy_j_total":   ("kwollect_benchmark_summary", "wattmetre_power_watt", "energy_j"),
        "avg_power_w":      ("kwollect_benchmark_summary", "wattmetre_power_watt", "average_power_w"),
        "peak_power_w":     ("kwollect_benchmark_summary", "wattmetre_power_watt", "peak_power_w"),
        "net_energy_j":     ("net_energy_j",),
        "rapl_pkg_j":       ("kwollect_benchmark_summary", "rapl_package_energy_joule", "energy_j"),
        "rapl_dram_j":      ("kwollect_benchmark_summary", "rapl_dram_energy_joule",    "energy_j"),
        "rapl_uncore_j":    ("kwollect_benchmark_summary", "rapl_uncore_energy_joule",  "energy_j"),
        "duration_s":       ("duration_seconds",),
    }

    for version, iterations in results.items():
        version_stats: dict[str, Any] = {"version": version}
        row = {"version": version}

        for col, path in metric_paths.items():
            vals = extract_metric_values(iterations, path)
            st = compute_stats(vals)
            version_stats[col] = st
            row[f"{col}_mean"] = st["mean"]
            row[f"{col}_std"]  = st["std"]
            row[f"{col}_n"]    = st["n"]

        full_stats[version] = version_stats
        summary_rows.append(row)

        print(f"  {version}")
        energy_st = version_stats["energy_j_total"]
        power_st  = version_stats["avg_power_w"]
        if energy_st["mean"] is not None:
            print(f"    Énergie totale : {energy_st['mean']:.2f} J  "
                  f"(±{energy_st['std']:.2f}, n={energy_st['n']})")
        if power_st["mean"] is not None:
            print(f"    Puissance moy  : {power_st['mean']:.2f} W  "
                  f"(±{power_st['std']:.2f})")
        for rapl_key, label in [
            ("rapl_pkg_j",    "CPU pkg"),
            ("rapl_dram_j",   "DRAM"),
            ("rapl_uncore_j", "Uncore/cache"),
        ]:
            rs = version_stats[rapl_key]
            if rs["mean"] is not None:
                print(f"    {label:<14}: {rs['mean']:.2f} J  (±{rs['std']:.2f})")

    # Sauvegarde
    write_json(analysis_dir / "summary.json", full_stats)

    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with (analysis_dir / "summary.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
        print(f"\n  → Statistiques : {analysis_dir / 'summary.csv'}")

    if skip_plot:
        print("  (graphiques ignorés : --skip-plot activé)")
        return

    # ---- Graphiques -------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  [INFO] matplotlib non disponible — graphiques ignorés.")
        print("         pip install matplotlib numpy")
        return

    versions = list(results.keys())
    x = np.arange(len(versions))
    bar_width = 0.6

    # --- 1. Histogramme puissance moyenne totale ---------------------------
    means = [full_stats[v]["avg_power_w"]["mean"] or 0 for v in versions]
    stds  = [full_stats[v]["avg_power_w"]["std"]  or 0 for v in versions]

    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(x, means, bar_width, yerr=stds, capsize=4,
                  color="steelblue", edgecolor="white", label="Puissance moy (W)")
    ax.set_xticks(x)
    ax.set_xticklabels(versions, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Puissance moyenne (W)")
    ax.set_title("Consommation moyenne par version Eclipse Collections")
    ax.legend()
    ax.bar_label(bars, fmt="%.1f", padding=3, fontsize=8)
    fig.tight_layout()
    out = analysis_dir / "histogram_avg_power.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  → Histogramme puissance : {out}")

    # --- 2. Histogramme énergie totale -------------------------------------
    e_means = [full_stats[v]["energy_j_total"]["mean"] or 0 for v in versions]
    e_stds  = [full_stats[v]["energy_j_total"]["std"]  or 0 for v in versions]

    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(x, e_means, bar_width, yerr=e_stds, capsize=4,
                  color="darkorange", edgecolor="white", label="Énergie (J)")
    ax.set_xticks(x)
    ax.set_xticklabels(versions, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Énergie consommée (J)")
    ax.set_title("Énergie consommée par version Eclipse Collections")
    ax.legend()
    ax.bar_label(bars, fmt="%.0f", padding=3, fontsize=8)
    fig.tight_layout()
    out = analysis_dir / "histogram_energy.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  → Histogramme énergie  : {out}")

    # --- 3. Décomposition RAPL CPU / DRAM / Uncore (si dispo) -------------
    rapl_keys   = ["rapl_pkg_j", "rapl_dram_j", "rapl_uncore_j"]
    rapl_labels = ["CPU (RAPL pkg)", "DRAM", "Uncore / cache"]
    rapl_colors = ["#e63946", "#457b9d", "#2a9d8f"]

    has_rapl = any(
        full_stats[v][k]["mean"] is not None
        for v in versions for k in rapl_keys
    )

    if has_rapl:
        rapl_data = {
            k: [full_stats[v][k]["mean"] or 0 for v in versions]
            for k in rapl_keys
        }
        rapl_std = {
            k: [full_stats[v][k]["std"] or 0 for v in versions]
            for k in rapl_keys
        }

        fig, ax = plt.subplots(figsize=(13, 5))
        bottom = np.zeros(len(versions))
        for k, label, color in zip(rapl_keys, rapl_labels, rapl_colors):
            vals = np.array(rapl_data[k])
            ax.bar(x, vals, bar_width, bottom=bottom, label=label, color=color,
                   edgecolor="white", alpha=0.9)
            bottom += vals

        ax.set_xticks(x)
        ax.set_xticklabels(versions, rotation=30, ha="right", fontsize=9)
        ax.set_ylabel("Énergie (J)")
        ax.set_title("Décomposition RAPL (CPU / DRAM / Uncore) par version")
        ax.legend()
        fig.tight_layout()
        out = analysis_dir / "breakdown_rapl.png"
        fig.savefig(out, dpi=150)
        plt.close(fig)
        print(f"  → Décomposition RAPL   : {out}")
    else:
        print("  [INFO] Aucune métrique RAPL disponible — graphique de décomposition ignoré.")

    # --- 4. Évolution temporelle (puissance moy vs version) ---------------
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.errorbar(
        versions, means, yerr=stds,
        fmt="-o", color="steelblue", capsize=5, linewidth=2, markersize=6,
    )
    ax.set_xticklabels(versions, rotation=30, ha="right", fontsize=9)
    ax.set_xticks(range(len(versions)))
    ax.set_xticklabels(versions, rotation=30, ha="right")
    ax.set_ylabel("Puissance moyenne (W)")
    ax.set_title("Évolution de la consommation selon la version EC")
    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()
    out = analysis_dir / "trend_power.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"  → Tendance temporelle  : {out}")

    print(f"\n  Analyse complète dans : {analysis_dir}")


# ---------------------------------------------------------------------------
# Arguments CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Campagne JMH Eclipse Collections × Grid5000 avec analyse énergie.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Versions
    parser.add_argument("--versions", nargs="+", default=DEFAULT_VERSIONS,
                        help="Versions Eclipse Collections à tester.")

    # Grid5000
    parser.add_argument("--site", required=True, help="Site Grid5000 (ex: lyon).")
    parser.add_argument("--node", default=short_hostname(),
                        help="Nom du nœud réservé.")
    parser.add_argument("--job-id", default=os.environ.get("OAR_JOB_ID"),
                        help="ID job OAR pour filtrer Kwollect.")
    parser.add_argument("--metrics", nargs="+", default=DEFAULT_METRICS,
                        help="Métriques Kwollect à collecter.")

    # Campagne
    parser.add_argument("--campaign-repeats", type=int, default=5,
                        help="Nombre d'itérations par version.")
    parser.add_argument("--skip-warmup", action="store_true",
                        help="Sauter la phase de chauffe machine.")
    parser.add_argument("--output-dir", default=None,
                        help="Répertoire de sortie de la campagne.")

    # JMH
    parser.add_argument("--iterations",        type=int, default=5)
    parser.add_argument("--warmup-iterations", type=int, default=5)
    parser.add_argument("--forks",             type=int, default=2)
    parser.add_argument("--iteration-time",  default="1s")
    parser.add_argument("--warmup-time",     default="1s")
    parser.add_argument("--time-unit",       default="ms")
    parser.add_argument("--benchmark-mode",  default="avgt")
    parser.add_argument("--includes",        default=None,
                        help="Regex JMH include (ex: ListBenchmark).")
    parser.add_argument("--excludes",        default=None,
                        help="Regex JMH exclude.")
    parser.add_argument("--benchmark-param", action="append", default=[],
                        help="Paramètre JMH (ex: size=1000,10000).")
    parser.add_argument("--jmh-result-format", default="JSON",
                        choices=["JSON", "CSV", "TEXT", "SCSV", "NONE"])

    # Temporisation
    parser.add_argument("--idle-seconds",            type=int, default=30,
                        help="Fenêtre idle avant chaque itération.")
    parser.add_argument("--rest-seconds",            type=int, default=15,
                        help="Pause après chaque run JMH.")
    parser.add_argument("--inter-iteration-rest",    type=int, default=30,
                        help="Pause entre deux itérations d'une même version.")
    parser.add_argument("--kwollect-settle-seconds", type=int, default=10,
                        help="Délai avant fetch Kwollect.")

    # Flags
    parser.add_argument("--skip-kwollect", action="store_true",
                        help="Désactiver Kwollect (test local).")
    parser.add_argument("--skip-plot", action="store_true",
                        help="Ne pas générer les graphiques matplotlib.")
    parser.add_argument("--analyse-only", default=None, metavar="CAMPAIGN_DIR",
                        help="Ré-analyser une campagne existante sans relancer les benchmarks.")

    # Système
    parser.add_argument("--gradle-command",
                        default="gradlew.bat" if os.name == "nt" else "./gradlew")
    parser.add_argument("--java-command", default="java")

    return parser.parse_args()


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()

    # Mode ré-analyse uniquement
    if args.analyse_only:
        analyse_and_plot(Path(args.analyse_only), skip_plot=args.skip_plot)
        return 0

    started_at = utc_now()
    default_output = (
        REPO_ROOT / "build" / "campaigns"
        / started_at.strftime("%Y%m%dT%H%M%SZ")
    )
    campaign_dir = Path(args.output_dir) if args.output_dir else default_output
    campaign_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "started_at": isoformat_utc(started_at),
        "repo_root": str(REPO_ROOT),
        "site": args.site,
        "node": args.node,
        "job_id": args.job_id,
        "metrics": args.metrics,
        "versions": args.versions,
        "campaign_repeats": args.campaign_repeats,
        "runs": [],
    }
    write_json(campaign_dir / "campaign-manifest.json", manifest)

    print(f"\n  Campagne : {campaign_dir}")
    print(f"  Versions : {args.versions}")
    print(f"  Nœud     : {args.node} @ {args.site}")
    print(f"  Répétitions par version : {args.campaign_repeats}")

    # Phase 1 : Warmup machine
    if not args.skip_warmup:
        run_warmup_phase(args, campaign_dir)

    # Phase 2 : Campagne principale
    run_campaign_phase(args, campaign_dir, manifest)

    manifest["ended_at"] = isoformat_utc(utc_now())
    write_json(campaign_dir / "campaign-manifest.json", manifest)

    # Phase 3 : Analyse
    analyse_and_plot(campaign_dir, skip_plot=args.skip_plot)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())