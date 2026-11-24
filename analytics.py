from collections import defaultdict

EXTRA = ("salinity", "ph", "n", "p", "k")
KEYS = ("temperature", "humidity", "water") + EXTRA
STATUS_ORDER = ("ok", "warn", "danger")


def mean(values):
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def average_latest(rows):
    """Среднее по последним показаниям датчиков. Статус — самый тревожный из них."""
    result = {key: mean([r[key] for r in rows]) for key in KEYS}
    statuses = [STATUS_ORDER.index(r["status"]) for r in rows if r["status"] in STATUS_ORDER]
    result["status"] = STATUS_ORDER[max(statuses)] if statuses else "ok"
    return result


def average_series(rows, bucket=10):
    """Среднее по всем датчикам в интервалах по bucket секунд."""
    groups = defaultdict(list)
    for r in rows:
        groups[int(r["ts"] // bucket)].append(r)
    out = []
    for key in sorted(groups):
        group = groups[key]
        point = {k: mean([g[k] for g in group]) for k in KEYS}
        point["ts"] = min(g["ts"] for g in group)
        out.append(point)
    return out
