/* StormLab persistent pattern intelligence — observational claims intentionally excluded.
 * Forecast captures are retrieval times, not individual model initialization cycles.
 */
(function () {
  "use strict";
  const RAW = "https://raw.githubusercontent.com/mrgillus/meyer-stormlab/main/data/events/";
  const CT = "America/Chicago";
  const q = id => document.getElementById(id);
  const safe = value => String(value ?? "").replace(/[&<>"']/g, c => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  })[c]);
  const numeric = x => typeof x === "number" && Number.isFinite(x);
  const state = {index:null,catalog:null,selected:null,loadedAt:0,inFlight:null,catalogInFlight:null};
  const titles = {snow:"Snowfall potential",precipitation:"Heavy precipitation",
    wind:"Strong winds",cold:"Unusual cold",heat:"Extreme heat"};
  const kinds = {snow:"❄",precipitation:"◈",wind:"〰",cold:"◇",heat:"☀"};
  const stamp = s => new Intl.DateTimeFormat("en-US",{timeZone:CT,month:"short",day:"numeric",
    hour:"numeric",minute:"2-digit",timeZoneName:"short"}).format(new Date(s));
  const short = date => new Intl.DateTimeFormat("en-US",{timeZone:CT,month:"short",day:"numeric"})
    .format(new Date(/^\d{4}-\d\d-\d\d$/.test(date)?date+"T12:00:00Z":date));
  const dateRange = item => short(item.start_date)+(item.start_date===item.end_date?"":" – "+short(item.end_date));
  const freshness = index => {
    const age=Date.now()-Date.parse(index?.source_latest_capture||"");
    return Number.isFinite(age)&&age>=0&&age<=12*3600000;
  };
  const describe = status => status==="in_latest_capture"?"Still in latest model guidance":
    status==="forecast_window_passed"?"Forecast window passed":
    "Faded from latest model guidance";
  const marker = status => status==="in_latest_capture"?"tracked":
    status==="forecast_window_passed"?"archived":"faded";
  function set(id,value){if(q(id))q(id).textContent=value}
  async function read(name){
    const res=await fetch(RAW+name+"?t="+Date.now(),
      {cache:"no-store",signal:AbortSignal.timeout(20000)});
    if(!res.ok)throw Error("HTTP "+res.status);
    const out=await res.json();
    if(out?.schema!==1||!Array.isArray(out.events))throw Error("Unrecognized event data format");
    return out;
  }
  function modelEvidence(item){
    const n=item.same_place_day_support||0;
    return n>=2?n+" models agreed on at least one place and day":
      "One model at a particular place and day";
  }
  function localEvidence(item){
    const loc=item.wentzville;
    if(!loc||!loc.triggering_samples)return "No Wentzville daily forecast met this screening threshold in the captured data.";
    const label=item.kind==="wind"?"mph":item.kind==="cold"||item.kind==="heat"?"°F":"in";
    const v=loc.median_triggering_value;
    return "Wentzville: "+loc.models.length+" model source(s) crossed the threshold; median triggering daily value "+
      (numeric(v)?v.toFixed(label==="mph"||label==="°F"?0:2):"—")+" "+label+
      ". This is not a storm-total forecast.";
  }
  function card(item){
    const tone=marker(item.status),label=titles[item.kind]||item.title;
    const sourceCount=item.models?.length||0;
    return '<article class="event-signal '+safe(tone)+'">'+
      '<div class="event-signal-head"><span class="event-symbol" aria-hidden="true">'+
      safe(kinds[item.kind]||"◎")+'</span><span class="event-flag">'+safe(describe(item.status))+'</span></div>'+
      '<h3>'+safe(label)+'</h3><p class="event-dates">'+safe(dateRange(item))+'</p>'+
      '<p class="event-story">'+safe(modelEvidence(item))+". "+safe(item.snapshots_seen)+" stored model capture(s). "+
      safe(item.locations?.length||0)+' regional location(s) flagged.</p>'+
      '<div class="event-evidence"><span>'+safe(sourceCount)+' model source(s)</span>'+
      '<span>First seen '+safe(short(item.first_seen))+'</span></div>'+
      '<button type="button" class="event-case-button" data-event-id="'+safe(item.id)+
      '">Explore the signal →</button></article>';
  }
  function renderHorizon(){
    const index=state.index;
    if(!index){set("eventWatchStatus","CATALOG UNAVAILABLE");
      q("eventWatchList").innerHTML='<div class="blank-state">Pattern tracking is waiting for a valid archived event feed. Live model scanning remains available below.</div>';return}
    const current=freshness(index);
    set("eventWatchStatus",current?index.capture_count_processed+" captures tracked":"ARCHIVE UPDATES DELAYED");
    const active=index.events.filter(x=>x.status==="in_latest_capture");
    const fading=index.events.filter(x=>x.status==="not_in_latest_capture");
    const display=[...active,...fading].slice(0,6);
    if(display.length){
      q("eventWatchList").innerHTML=display.map(card).join("");
    }else{
      q("eventWatchList").innerHTML='<div class="blank-state">No qualifying active or fading model signals in the published regional case catalog. The live model scan below can still be explored.</div>';
    }
    set("eventWatchSource","Source snapshot: "+stamp(index.source_latest_capture)+
      (current?"":" · STALE — waiting for scheduled collector")+
      " · Stitched deterministic daily forecasts only; these are not warnings, observations, or calibrated probabilities.");
  }
  function pickRow(item){
    const selected=state.selected===item.id?" is-selected":"";
    const label=titles[item.kind]||item.title;
    const note=item.status==="in_latest_capture"?"Developing model signal":
      item.status==="not_in_latest_capture"?"Faded from newer guidance":"Forecast window passed";
    return '<button class="case-choice'+selected+'" type="button" data-case-id="'+safe(item.id)+'">'+
      '<span class="case-choice-symbol" aria-hidden="true">'+safe(kinds[item.kind]||"◎")+'</span>'+
      '<span class="case-choice-content"><strong>'+safe(label)+'</strong><small>'+
      safe(dateRange(item))+' · '+safe(note)+'</small></span>'+
      '<span class="case-choice-chevron" aria-hidden="true">›</span></button>';
  }
  function renderArchiveList(){
    if(!state.index){set("trackedCasesStatus","CATALOG UNAVAILABLE");
      q("eventCaseList").innerHTML='<div class="blank-state">No event index could be loaded yet.</div>';return}
    const items=state.index.events;
    set("trackedCasesStatus",items.length+" tracked signal(s)");
    q("eventCaseList").innerHTML=items.length?items.map(pickRow).join(""):
      '<div class="blank-state">The event collector has not identified any threshold-crossing forecasts yet.</div>';
    if(!state.selected&&items.length)state.selected=items[0].id;
    renderSelected();
  }
  async function loadCatalog(){
    if(state.catalog)return state.catalog;
    if(state.catalogInFlight)return state.catalogInFlight;
    state.catalogInFlight=read("catalog.json").then(c=>{state.catalog=c;return c})
      .finally(()=>{state.catalogInFlight=null});
    return state.catalogInFlight;
  }
  function timelineRow(item,kind){
    const peak=item.peak;
    const unit=kind==="wind"?"mph":kind==="cold"||kind==="heat"?"°F":"in";
    const max=peak&&numeric(peak.value)?peak.value.toFixed(unit==="mph"||unit==="°F"?0:2)+" "+unit:"—";
    return '<tr><td>'+safe(stamp(item.captured_at))+'</td><td>'+safe(item.models?.length||0)+
      '</td><td>'+safe(item.same_place_day_support||0)+'</td><td>'+safe(max)+
      '</td><td>'+safe(dateRange(item))+'</td></tr>';
  }
  async function renderSelected(){
    const index=state.index;
    const summary=index?.events.find(x=>x.id===state.selected);
    if(!summary){q("eventCaseDetail").innerHTML="";window.StormLabVerification?.render(null);return}
    window.StormLabVerification?.render(summary.id);
    const label=titles[summary.kind]||summary.title;
    const unit=summary.threshold?.unit||"";
    const trigger=summary.threshold?.direction==="lte"?"≤":"≥";
    q("eventCaseDetail").innerHTML='<p class="muted">Loading the full captured model history for this event…</p>';
    let caseFile;
    try{
      const catalog=await loadCatalog();
      if(state.selected!==summary.id)return;
      caseFile=catalog.events.find(x=>x.id===summary.id);
      if(!caseFile)throw Error("Case record not present in published catalog");
    }catch(err){
      if(state.selected===summary.id)q("eventCaseDetail").innerHTML='<p class="notice-error">Cannot open this event history: '+safe(err.message)+'</p>';
      return;
    }
    const timeline=Array.isArray(caseFile.timeline)?caseFile.timeline.slice().reverse():[];
    const last=caseFile.timeline?.at(-1),first=caseFile.timeline?.[0];
    q("eventCaseDetail").innerHTML='<div class="case-detail-top"><div><span class="kicker">EVENT INTELLIGENCE / '+safe(summary.id)+'</span>'+
      '<h3>'+safe(label)+'</h3><p>'+safe(dateRange(summary))+'</p></div><span class="event-flag">'+
      safe(describe(summary.status))+'</span></div>'+
      '<div class="case-detail-numbers"><div><span>FIRST SEEN</span><strong>'+safe(stamp(caseFile.first_seen))+'</strong></div>'+
      '<div><span>LAST REPORTED</span><strong>'+safe(stamp(caseFile.last_seen))+'</strong></div>'+
      '<div><span>FORECAST CAPTURES</span><strong>'+safe(timeline.length)+'</strong></div></div>'+
      '<p class="case-detail-copy">We started tracking this when a daily forecast crossed our '+safe(trigger)+
      safe(summary.threshold?.value)+' '+safe(unit)+' screening level. '+
      safe(modelEvidence(summary))+". "+
      safe(localEvidence(summary))+'</p>'+
      '<p class="case-detail-copy">Ground observations are not connected. A captured forecast is not a storm observation, and this signal is not an NWS watch, warning, or calibrated risk forecast.</p>'+
      '<div class="capture-scroll"><table class="capture-table"><thead><tr><th>Forecast captured</th><th>Models</th><th>Same-place support</th><th>Strongest daily sample*</th><th>Forecast days</th></tr></thead><tbody>'+
      timeline.slice(0,25).map(t=>timelineRow(t,summary.kind)).join("")+'</tbody></table></div>'+
      '<p class="fine-print">*The strongest triggering model/day/location sample is not a forecast for your selected town. '+
      'Values represent archived stitched guidance, not model initialization times. No measured outcome or accuracy verdict is available.'+
      (timeline.length>25?" Showing 25 most recent of "+timeline.length+" captures.":"")+'</p>';
    q("eventCaseList").querySelectorAll(".case-choice").forEach(button=>
      button.classList.toggle("is-selected",button.dataset.caseId===summary.id));
  }
  async function reload(force=false){
    if(state.inFlight)return state.inFlight;
    if(!force&&state.index&&Date.now()-state.loadedAt<5*60*1000){
      renderHorizon();renderArchiveList();return;
    }
    state.inFlight=read("index.json").then(d=>{
      state.index=d;state.loadedAt=Date.now();
      renderHorizon();renderArchiveList();
    }).catch(e=>{
      if(!state.index){
        set("eventWatchSource","The independent event-catalog feed is unavailable: "+e.message);
        renderHorizon();renderArchiveList();
      }
    }).finally(()=>{state.inFlight=null});
    return state.inFlight;
  }
  function init(){
    q("eventWatchList")?.addEventListener("click",ev=>{
      const button=ev.target.closest("[data-event-id]");if(!button)return;
      state.selected=button.dataset.eventId;
      document.querySelector('[data-tab="archive"]')?.click();
      renderArchiveList();
      q("trackedCasesTitle")?.scrollIntoView({behavior:"smooth",block:"start"});
    });
    q("eventCaseList")?.addEventListener("click",ev=>{
      const button=ev.target.closest("[data-case-id]");if(!button)return;
      state.selected=button.dataset.caseId;renderArchiveList();
    });
    document.querySelectorAll('[data-tab="horizon"],[data-tab="archive"]').forEach(button=>
      button.addEventListener("click",()=>reload()));
    document.addEventListener("visibilitychange",()=>{
      if(document.visibilityState==="visible")reload();
    });
    reload();
    window.setInterval(()=>{if(document.visibilityState==="visible")reload(true)},15*60*1000);
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});
  else init();
})();
