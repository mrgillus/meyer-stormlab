"""Re-score existing regional snowfall models against clock-independent storm totals.

Question: how much of the reported heavy-snow underprediction is a label
artifact of misaligned daily reports, rather than model error?

For each held-out storm (validation + test seasons; models were fit on train
seasons only with fixed hyperparameters), sum the existing daily predictions
over the storm's UTC days from one initialization (init S: +24/+48/+72 h for
days S, S+1, S+2), and compare that sum with:
  * the clock-independent storm total (storm_total_labels), and
  * the legacy daily-mapping total the model was trained against.
No model is refit and nothing is promoted.
"""
import datetime as dt
import gzip
import csv
import json
import math
from pathlib import Path
import joblib
import numpy as np
import regional_live as live
import storm_total_labels as L

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/storm_total_rescore"
MAPPINGS = {"utc_end_date": 1, "utc_end_previous_date": 0}  # legacy offsets relative to UTC day
MODEL_DIRS = {24: "regional_learning", 48: "regional_learning48", 72: "regional_learning"}
CLASSES = (("all", 0, math.inf), ("observed_lt_1in", 0, 1), ("observed_1_4in", 1, 4), ("observed_ge_4in", 4, math.inf))


def load_models():
    models = {}
    for lead, folder in MODEL_DIRS.items():
        for mapping in MAPPINGS:
            m = joblib.load(ROOT / "data" / folder / f"research_{lead}_{mapping}.joblib")
            if m["lead_hours"] != lead or m["label_mapping"] != mapping or m["feature_version"] != 1:
                raise ValueError("Unexpected model artifact")
            models[(lead, mapping)] = m
    return models


def predict_event(models, mapping, data_by_lead, points, i, start, days):
    """Sum of daily predictions over the storm, one init. Also the 10:1 cold-precip reference."""
    rf = boost = phys = 0.0
    ood = False
    for k in range(days):
        lead = 24 * (k + 1)
        data = data_by_lead[lead]
        # regional_learning: label date = UTC window end date + mapping offset; DOY uses that date.
        end = start + dt.timedelta(days=k + 1)
        target = end + dt.timedelta(days=MAPPINGS[mapping] - 1)
        x = live.vector(data, points, i, target)[None, :]
        m = models[(lead, mapping)]
        lo, hi = m["training_min"], m["training_max"]
        ood |= bool(((x < lo - np.maximum(1e-6, (hi - lo) * .001)) | (x > hi + np.maximum(1e-6, (hi - lo) * .001))).any())
        rf += max(0.0, float(m["rf"].predict(x)[0]))
        boost += max(0.0, float(m["boosted"].predict(x)[0]))
        cold = (data[0, :, i] <= 274.15) & (data[1, :, i] <= 273.15)
        phys += float(np.mean(np.maximum(data[5, :, i], 0) * 10 / 25.4 * cold))
    return rf, boost, phys, ood


def metrics(rows, pred_key, truth_key):
    out = {}
    for name, lo, hi in CLASSES:
        rr = [r for r in rows if lo <= r["observed_in"] < hi and r[truth_key] is not None]
        if not rr:
            continue
        err = np.array([r[pred_key] - r[truth_key] for r in rr])
        truth = np.array([r[truth_key] for r in rr])
        out[name] = dict(n=len(rr), windows=len({(r["start_utc"], r["end_utc"]) for r in rr}),
                         bias_in=round(float(err.mean()), 3), mae_in=round(float(np.abs(err).mean()), 3),
                         mean_truth_in=round(float(truth.mean()), 3),
                         ratio_pred_to_truth=round(float(sum(r[pred_key] for r in rr) / truth.sum()), 3) if truth.sum() > 0 else None)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest, core, _ = L.load_shards(L.ARCH, (24, 72))
    _, ext48, _ = L.load_shards(L.ARCH48, (48,))
    points = manifest["points"]
    models = load_models()
    with gzip.open(ROOT / "data/storm_total_labels/labels.csv.gz", "rt") as f:
        labels = [r for r in csv.DictReader(f) if r["split"] in ("validation", "test") and r["same_init_features"] == "True"]
    rows = []
    for r in labels:
        s = dt.date.fromisoformat(r["start_utc"])
        n = int(r["days"])
        data_by_lead = {**core.get(s, {}), **ext48.get(s, {})}
        i = int(r["point_index"])
        if points[i]["label"] != r["station"]:
            raise ValueError("Station/point mismatch")
        row = dict(station=r["station"], start_utc=r["start_utc"], end_utc=r["end_utc"], days=n, split=r["split"],
                   observed_in=float(r["observed_in"]))
        for mapping in MAPPINGS:
            rf, boost, phys, ood = predict_event(models, mapping, data_by_lead, points, i, s, n)
            legacy = r[f"legacy_{mapping}_in"]
            row.update({f"rf_{mapping}": round(rf, 3), f"boosted_{mapping}": round(boost, 3),
                        f"legacy_label_{mapping}": float(legacy) if legacy not in ("", "None") else None,
                        f"ood_{mapping}": ood})
        row["cold_precip_10to1"] = round(phys, 3)
        rows.append(row)
    results = {}
    for mapping in MAPPINGS:
        res = {}
        for model in ("rf", "boosted"):
            key = f"{model}_{mapping}"
            res[model] = dict(vs_storm_total=metrics(rows, key, "observed_in"),
                              vs_legacy_daily_label=metrics(rows, key, f"legacy_label_{mapping}"))
        res["legacy_label_vs_storm_total"] = metrics(rows, f"legacy_label_{mapping}", "observed_in")
        res["ood_share"] = round(float(np.mean([r[f"ood_{mapping}"] for r in rows])), 3)
        results[mapping] = res
    results["cold_precip_10to1"] = dict(vs_storm_total=metrics(rows, "cold_precip_10to1", "observed_in"))
    with gzip.open(OUT / "event_predictions.csv.gz", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    report = dict(schema=1, generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  labels_report_generated_at=json.loads((ROOT / "data/storm_total_labels/report.json").read_text())["generated_at"],
                  model_artifacts={f"{l}_{m}": str((ROOT / "data" / MODEL_DIRS[l] / f"research_{l}_{m}.joblib").relative_to(ROOT)) for l, m in models},
                  events=len(rows), distinct_windows=len({(r["start_utc"], r["end_utc"]) for r in rows}),
                  results=results,
                  interpretation_notes=["Positive bias = prediction too high. Storm totals are clock-independent; legacy labels are the daily mapping each model was trained on.",
                                        "Validation seasons were inspected during earlier research; test seasons are held out. Small heavy-snow samples: read n and windows before conclusions."])
    (OUT / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(dict(events=report["events"], windows=report["distinct_windows"], results=results), indent=1))


if __name__ == "__main__":
    main()
