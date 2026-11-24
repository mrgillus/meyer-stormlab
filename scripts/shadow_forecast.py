"""Shadow-mode forecast using the existing trained RF, with exact historical feature
construction and a strict as-of gate. Does not issue calibrated operational guidance."""
import datetime as dt
import json
import math
import pathlib
import joblib
import hindcast as h

OUT=h.ROOT/"data"/"experimental_live"
def main():
    now=dt.datetime.now(dt.timezone.utc)
    today=now.astimezone(h.TZ).date()
    dates=[today+dt.timedelta(days=i) for i in range(1,6)]
    errors=[];rows=[];forecasts={}
    report=json.loads((h.OUT/"report.json").read_text())
    for name in ("GEM","ICON"):
        try:forecasts[name]=h.forecast(h.MODELS[name],dates[0].isoformat(),dates[-1].isoformat())
        except Exception as e:errors.append({"model":name,"error":str(e)})
    for lead in (1,3,5):
        path=h.OUT/f"rf_day{lead}.joblib"
        bundle=joblib.load(path)
        if bundle["feature_version"]!=1 or bundle["station"]!=h.STATION or bundle["lead_offset_days"]!=lead:
            raise ValueError("Incompatible trained model")
        names=bundle["model_names"];model=bundle["model"]
        benchmark=next(r for r in report["results"] if r["lead"]==lead)
        for day in dates:
            end=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),h.TZ).astimezone(dt.timezone.utc)
            item=dict(date=day.isoformat(),lead_offset_days=lead,station=h.STATION,model_names=names)
            # Every hourly forecast must have been available in principle before retrieval.
            # Never substitute current stitched forecasts for historical fixed-offset inputs.
            if end-dt.timedelta(days=lead)>now:
                rows.append(dict(item,status="not_yet_eligible"));continue
            vals={n:forecasts.get(n,{}).get((day.isoformat(),lead)) for n in names}
            if not all(vals.values()):
                rows.append(dict(item,status="missing_fixed_offset_inputs"));continue
            x=h.features({"models":vals},names)
            estimate=max(0,float(model.predict([x])[0]))
            if not math.isfinite(estimate):raise ValueError("Invalid prediction")
            rows.append(dict(item,status="experimental",raw_snow_inches={n:vals[n][0] for n in names},
                raw_features=vals,rf_snow_inches=round(estimate,3),
                equal_blend_inches=round(sum(vals[n][0] for n in names)/len(names),3),
                training_snow_days=benchmark["train_snow_days"],test_snow_days=benchmark["test_snow_days"],
                heldout_mae=benchmark["metrics"]["StormLab RF"]["mae"]))
    OUT.mkdir(parents=True,exist_ok=True)
    payload=dict(generated_at=now.isoformat(),station="St Louis Lambert International Airport",
        predictions=rows,errors=errors,limitations=["Experimental shadow-mode output only, not operational guidance.",
        "Fixed hourly lead-offset forecasts may be older than the newest model runs; no Wentzville calibration.",
        "Only six significant training snowfall days and four held-out snowfall days.",
        "Random forest cannot extrapolate reliably beyond training snowfall extremes; not validated to outperform raw guidance.",
        "Observation reporting alignment is unresolved. Refer to NWS forecasts for decisions."])
    (OUT/"report.json").write_text(json.dumps(payload,indent=2,allow_nan=False)+"\n")
    print(json.dumps(payload,indent=2),flush=True)
if __name__=="__main__":main()
