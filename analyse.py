#!/usr/bin/env python3

from pathlib import Path
import json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ------------------------------------------------------------
# PATHS
# ------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
CAMPAIGNS_DIR = BASE_DIR / "build" / "campaigns"


# ------------------------------------------------------------
# FIND CAMPAIGN
# ------------------------------------------------------------

def find_latest_campaign():
    if not CAMPAIGNS_DIR.exists():
        raise RuntimeError(f"Dossier introuvable : {CAMPAIGNS_DIR}")

    campaigns = sorted([p for p in CAMPAIGNS_DIR.iterdir() if p.is_dir()])

    valid = [c for c in campaigns if (c / "campaign-summary.json").exists()]

    if not valid:
        raise RuntimeError("Aucune campagne valide trouvée")

    latest = valid[-1]
    print(f"✔ Campagne valide : {latest.name}")
    return latest


# ------------------------------------------------------------
# LOAD DATA
# ------------------------------------------------------------

def load_results(campaign_dir):
    manifest_path = campaign_dir / "campaign-summary.json"

    if not manifest_path.exists():
        raise RuntimeError(f"Fichier manquant : {manifest_path}")

    data = json.loads(manifest_path.read_text())

    results = {}

    runs = data if isinstance(data, list) else data.get("runs", [])

    for run in runs:
        version = run.get("version", "unknown")
        results[version] = run.get("iterations", [])

    return results


# ------------------------------------------------------------
# SAFE GET
# ------------------------------------------------------------

def get(iteration, path):
    node = iteration
    for p in path:
        if isinstance(node, dict):
            node = node.get(p)
            if node is None:
                return None
        else:
            return None
    return node


def stats(values):
    values = [v for v in values if v is not None]

    if len(values) == 0:
        return None, None

    return np.mean(values), np.std(values)


# ------------------------------------------------------------
# POWER GRAPH
# ------------------------------------------------------------

def plot_power(results, outdir):
    versions = list(results.keys())

    means, stds = [], []

    for v in versions:
        vals = [
            get(it, ("kwollect_wattmetre_power_watt", "average_power_w"))
            for it in results[v]
        ]

        m, s = stats(vals)

        print(f"[POWER] {v} -> {vals}")

        means.append(m)
        stds.append(s)

    x = np.arange(len(versions))
    width = 0.35

    plt.figure(figsize=(10, 5))

    plt.bar(x, means, width=width, yerr=stds, capsize=4)

    plt.xticks(x, versions, rotation=30)
    plt.title("Puissance moyenne par version")
    plt.ylabel("W")

    # zoom intelligent
    valid = [(m, s) for m, s in zip(means, stds) if m is not None and s is not None]

    if valid:
        lower = min(m - s for m, s in valid)
        upper = max(m + s for m, s in valid)

        margin = (upper - lower) * 0.15

        plt.ylim(lower - margin, upper + margin)


    plt.tight_layout()
    plt.savefig(outdir / "power.png")
    plt.close()


# ------------------------------------------------------------
# ENERGY GRAPH
# ------------------------------------------------------------

def plot_energy(results, outdir):
    versions = list(results.keys())

    means, stds = [], []

    for v in versions:
        vals = [
            get(it, ("kwollect_wattmetre_power_watt", "energy_j"))
            for it in results[v]
        ]

        m, s = stats(vals)

        print(f"[ENERGY] {v} -> {vals}")

        means.append(m)
        stds.append(s)

    x = np.arange(len(versions))
    width = 0.35

    plt.figure(figsize=(10, 5))

    plt.bar(x, means, width=width, yerr=stds, capsize=4, color="orange")

    plt.xticks(x, versions, rotation=30)
    plt.title("Énergie consommée par version")
    plt.ylabel("Joules")

    valid = [(m, s) for m, s in zip(means, stds) if m is not None and s is not None]

    if valid:
        lower = min(m - s for m, s in valid)
        upper = max(m + s for m, s in valid)

        margin = (upper - lower) * 0.15

        plt.ylim(lower - margin, upper + margin)

    plt.tight_layout()
    plt.savefig(outdir / "energy.png")
    plt.close()


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():
    print(f"Projet : {BASE_DIR}")
    print(f"Campagnes : {CAMPAIGNS_DIR}")

    campaign = find_latest_campaign()
    results = load_results(campaign)

    outdir = campaign / "analysis"
    outdir.mkdir(parents=True, exist_ok=True)

    plot_power(results, outdir)
    plot_energy(results, outdir)

    print(f"Graphes générés dans : {outdir}")


if __name__ == "__main__":
    main()