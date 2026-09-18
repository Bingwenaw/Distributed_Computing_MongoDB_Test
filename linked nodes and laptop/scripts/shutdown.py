"""Stop only this copy's two Docker projects; keep all volumes."""
import sys
from cluster_config import ROOT, LOCAL, SETTINGS, load_linked
sys.path.insert(0, str(ROOT))
from faults.control import stop_cluster


def shutdown():
    clusters = [LOCAL]
    if SETTINGS.exists() or (ROOT / "compose.linked.json").exists():
        clusters.append(load_linked())
    errors = []
    for cluster in clusters:
        try:
            stop_cluster(cluster)
        except Exception as exc:
            errors.append(f"{cluster.project}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))


if __name__ == "__main__":
    try:
        shutdown()
    except Exception as exc:
        raise SystemExit(f"Shutdown incomplete: {exc}")
