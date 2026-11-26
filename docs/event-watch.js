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
  const titles = {snow:"Accumulating snowfall",precipitation:"Heavy precipitation",
    wind:"Strong winds",cold:"Unusual cold",heat:"Extreme heat"};
  const kinds = {snow:"❄",light_snow:"❄",precipitation:"◈",rain:"☂",showers:"☂",
    drizzle:"☂",ice:"◇",thunderstorm:"ϟ",fog:"≋",freeze:"◇",heat_stress:"☀",
    cold_stress:"◇",wind:"〰",cold:"◇",heat:"☀"};
  const routine = item => item.priority==="routine";
  const unitsFor = kind => ["wind"].includes(kind)?"mph":
    ["cold","heat","freeze","heat_stress","cold_stress"].includes(kind)?"°F":
    ["light_snow","drizzle","ice","thunderstorm","fog"].includes(kind)?"signal":"in";
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
    const label=unitsFor(item.kind);
    if(label==="signal")return "Wentzville: "+loc.models.length+" source(s) flagged the matching daily weather code. This is not an intensity estimate.";
    const v=loc.median_triggering_value;
    return "Wentzville: "+loc.models.length+" model source(s) crossed the threshold; median triggering daily value "+
      (numeric(v)?v.toFixed(label==="mph"||label==="°F"?0:2):"—")+" "+label+
      ". This is not a storm-total forecast.";
  }
  const watchName = item => item.name || item.id;
  function watchChart(points){
    if(!Array.isArray(points)||!points.length)return '<p class="watch-muted">Assessment history awaits a collector update.</p>';
    const last=points.at(-1), previous=points.at(-2), pct=last.support_pct;
    const label=!numeric(pct)?"Unknown":(last.unknown_models?"≥":"")+pct+"%";
    const change=numeric(pct)&&numeric(previous?.support_pct)&&!last.unknown_models&&!previous.unknown_models?
      (pct>previous.support_pct?"Strengthening":pct<previous.support_pct?"Weakening":pct===0?"Faded":"Steady"):"";
    const x0=32,x1=310,y0=9,y1=88,t0=Date.parse(points[0].captured_at),t1=Date.parse(last.captured_at);
    const x=p=>t0===t1?(x0+x1)/2:x0+(Date.parse(p.captured_at)-t0)/(t1-t0)*(x1-x0);
    const y=v=>y1-v/100*(y1-y0);
    let lines="",dots="",prior=null;
    points.forEach(p=>{
      const px=x(p),lo=numeric(p.support_pct)?p.support_pct:0;
      if(p.unknown_models)lines+='<line class="watch-uncertain" x1="'+px+'" x2="'+px+'" y1="'+y(lo)+'" y2="'+y(p.possible_support_pct)+'"><title>'+safe(stamp(p.captured_at))+' · incomplete coverage</title></line>';
      if(numeric(p.support_pct)){
        const py=y(p.support_pct);
        if(prior)lines+='<line class="watch-trend-line" x1="'+prior[0]+'" y1="'+prior[1]+'" x2="'+px+'" y2="'+py+'"/>';
        dots+='<circle class="watch-dot" cx="'+px+'" cy="'+py+'" r="2.6"><title>'+safe(stamp(p.captured_at))+' · '+safe(p.supporting_models)+' of '+safe(p.total_models)+' support; '+safe(p.unknown_models)+' unknown</title></circle>';
        prior=[px,py];
      }else prior=null;
    });
    const grid=[0,50,100].map(v=>'<line class="watch-grid" x1="'+x0+'" x2="'+x1+'" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="1" y="'+(y(v)+3)+'">'+v+'%</text>').join("");
    const chips=Object.entries(last.models||{}).map(([m,s])=>'<span class="watch-model '+safe(s)+'">'+safe(m)+' · '+safe(s==="supports"?"YES":s==="clear"?"NO":"?")+'</span>').join("");
    return '<div class="watch-assessment"><div class="watch-score"><strong>'+safe(label)+'</strong><div><b>Confirmed model support</b><small>'+safe(last.supporting_models)+'/'+safe(last.total_models)+' supporting · '+safe(last.total_models-last.unknown_models)+'/'+safe(last.total_models)+' assessed'+(change?' · '+safe(change):'')+'</small></div></div>'+
      '<svg class="watch-graph" viewBox="0 0 320 110" role="img" aria-label="Confirmed model support by forecast capture">'+grid+lines+dots+
      '<text x="'+x0+'" y="105">'+safe(short(points[0].captured_at))+'</text><text x="'+x1+'" y="105" text-anchor="end">'+safe(short(last.captured_at))+'</text></svg>'+
      '<div class="watch-models">'+chips+'</div><small class="watch-muted">'+safe(stamp(last.captured_at))+' · Not an event probability. Dashed ranges = unknown coverage.</small></div>';
  }
  function groups(index){
    if(Array.isArray(index?.event_groups))return index.event_groups;
    // Older published indexes: group by overlapping dates until rebuild.
    const out=[];
    [...(index?.events||[])].sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.id.localeCompare(b.id)).forEach(m=>{
      let g=out.at(-1);
      if(!g||m.start_date>g.end_date){
        g={id:"event-"+m.id,name:watchName(m),primary_mode_id:m.id,mode_ids:[],
          start_date:m.start_date,end_date:m.end_date,first_seen:m.first_seen,
          status:m.status,priority:m.priority};
        out.push(g);
      }
      g.mode_ids.push(m.id);
      if(m.end_date>g.end_date)g.end_date=m.end_date;
      if(m.status==="in_latest_capture")g.status=m.status;
      else if(g.status==="forecast_window_passed"&&m.status==="not_in_latest_capture")g.status=m.status;
      if(!routine(m))g.priority="notable";
      if(m.kind==="snow"){g.primary_mode_id=m.id;g.name=watchName(m)}
    });
    return out;
  }
  function modeItems(group){
    return (group?.mode_ids||[]).map(id=>state.index?.events.find(m=>m.id===id)).filter(Boolean);
  }
  function groupFor(id){
    return groups(state.index).find(g=>g.id===id||g.mode_ids.includes(id));
  }
  function modeLabel(item){return titles[item.kind]||item.title}
  function groupCard(group){
    const modes=modeItems(group),primary=modes.find(m=>m.id===group.primary_mode_id)||modes[0];
    const tone=marker(group.status);
    return '<article class="event-signal '+safe(tone)+'"><div class="event-signal-head"><span class="event-symbol" aria-hidden="true">'+safe(kinds[primary?.kind]||"◎")+'</span><span class="event-flag">'+safe(describe(group.status))+'</span></div>'+
      '<h3>Event '+safe(group.name)+'</h3><p class="event-dates">'+safe(dateRange(group))+' · '+modes.length+' weather mode(s)</p>'+
      '<div class="event-mode-list">'+modes.map(m=>'<details class="event-mode" '+(m.id===primary?.id?'open':'')+'><summary><span>'+safe(kinds[m.kind]||"◎")+' '+safe(modeLabel(m))+'</span><strong>'+safe(modeScore(m))+'</strong></summary><p class="watch-muted">'+safe(dateRange(m))+' · '+safe(describe(m.status))+'</p>'+watchChart(m.assessments)+'</details>').join("")+'</div>'+
      '<div class="event-evidence"><span>First seen '+safe(short(group.first_seen))+'</span><span>One regional date window</span></div>'+
      '<button type="button" class="event-case-button" data-event-id="'+safe(primary?.id||group.primary_mode_id)+'">Explore event →</button></article>';
  }
  function modeScore(m){
    const p=m.assessments?.at(-1);
    return !p||!numeric(p.support_pct)?"Unknown":(p.unknown_models?"≥":"")+p.support_pct+"%";
  }
  function card(item){
    const tone=marker(item.status),label=titles[item.kind]||item.title;
    const today=new Intl.DateTimeFormat("en-CA",{timeZone:CT,year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
    const windowLabel=item.start_date<=today&&item.end_date>=today?"Forecast window now":"Upcoming forecast window";
    const sourceCount=item.models?.length||0;
    return '<article class="event-signal '+safe(tone)+'">'+
      '<div class="event-signal-head"><span class="event-symbol" aria-hidden="true">'+
      safe(kinds[item.kind]||"◎")+'</span><span class="event-flag">'+safe(describe(item.status))+'</span></div>'+
      '<h3>Event '+safe(watchName(item))+'</h3><p class="event-dates">'+safe(label)+' · '+safe(dateRange(item))+'</p><small class="watch-muted">'+safe(windowLabel+(routine(item)?" · Routine signal":" · Notable signal"))+'</small>'+
      watchChart(item.assessments)+
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
    const active=groups(index).filter(x=>x.status==="in_latest_capture");
    const fading=groups(index).filter(x=>x.status==="not_in_latest_capture");
    const today=new Intl.DateTimeFormat("en-CA",{timeZone:CT,year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
    const display=[...active,...fading].filter(x=>x.end_date>=today).sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.id.localeCompare(b.id));
    if(display.length){
      const notable=display.filter(x=>!routine(x)), everyday=display.filter(routine);
      q("eventWatchList").innerHTML=(notable.length?notable.map(groupCard).join(""):"")+
        (everyday.length?'<details class="routine-watches" open><summary>Routine weather events · '+everyday.length+'</summary><div class="event-watch-list">'+everyday.map(groupCard).join("")+'</div></details>':"");
    }else{
      q("eventWatchList").innerHTML='<div class="blank-state">No qualifying active or fading model signals in the published regional case catalog. The live model scan below can still be explored.</div>';
    }
    set("eventWatchSource","Source snapshot: "+stamp(index.source_latest_capture)+
      (current?"":" · STALE — waiting for scheduled collector")+
      " · Stitched deterministic daily forecasts only; these are not warnings, observations, or calibrated probabilities.");
  }
  function pickRow(item){
    const selected=item.mode_ids.includes(state.selected)?" is-selected":"";
    const modes=modeItems(item),primary=modes.find(m=>m.id===item.primary_mode_id)||modes[0];
    const label="Event "+item.name+" · "+modes.map(modeLabel).join(" / ");
    const note=item.status==="in_latest_capture"?"Developing model signal":
      item.status==="not_in_latest_capture"?"Faded from newer guidance":"Forecast window passed";
    return '<button class="case-choice'+selected+'" type="button" data-case-id="'+safe(item.primary_mode_id)+'">'+
      '<span class="case-choice-symbol" aria-hidden="true">'+safe(kinds[primary?.kind]||"◎")+'</span>'+
      '<span class="case-choice-content"><strong>'+safe(label)+'</strong><small>'+
      safe(dateRange(item))+' · '+safe(note)+'</small></span>'+
      '<span class="case-choice-chevron" aria-hidden="true">›</span></button>';
  }
  function renderArchiveList(){
    if(!state.index){set("trackedCasesStatus","CATALOG UNAVAILABLE");
      q("eventCaseList").innerHTML='<div class="blank-state">No event index could be loaded yet.</div>';return}
    const items=groups(state.index);
    set("trackedCasesStatus",items.length+" tracked event(s)");
    q("eventCaseList").innerHTML=items.length?items.map(pickRow).join(""):
      '<div class="blank-state">The event collector has not identified any threshold-crossing forecasts yet.</div>';
    if(!groupFor(state.selected)&&items.length)state.selected=items[0].primary_mode_id;
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
    if(item.models&&!Array.isArray(item.models)){
      const support=numeric(item.support_pct)?(item.unknown_models?"≥":"")+item.support_pct+"%":"Unknown";
      return '<tr><td>'+safe(stamp(item.captured_at))+'</td><td>'+safe(support)+'</td><td>'+safe(item.unknown_models)+'</td><td>'+safe(Object.entries(item.models).map(([m,s])=>m+": "+s).join(" · "))+'</td><td>'+safe(dateRange(item))+'</td></tr>';
    }
    const peak=item.peak;
    const unit=unitsFor(kind);
    const max=peak&&numeric(peak.value)?(unit==="signal"?"Matching code":peak.value.toFixed(unit==="mph"||unit==="°F"?0:2)+" "+unit):"—";
    return '<tr><td>'+safe(stamp(item.captured_at))+'</td><td>'+safe(item.models?.length||0)+
      '</td><td>'+safe(item.same_place_day_support||0)+'</td><td>'+safe(max)+
      '</td><td>'+safe(dateRange(item))+'</td></tr>';
  }
  async function renderSelected(){
    const index=state.index;
    const summary=index?.events.find(x=>x.id===state.selected);
    if(!summary){q("eventCaseDetail").innerHTML="";window.StormLabVerification?.render(null);return}
    window.StormLabVerification?.render(summary.id);
    const group=groupFor(summary.id);
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
    const assessments=(caseFile.assessments||[]).slice().reverse();
    const last=caseFile.timeline?.at(-1),first=caseFile.timeline?.[0];
    q("eventCaseDetail").innerHTML='<div class="case-detail-top"><div><span class="kicker">EVENT INTELLIGENCE / '+safe(summary.id)+'</span>'+
      '<h3>Event '+safe(group?.name||watchName(summary))+'</h3><p>'+safe(dateRange(group||summary))+'</p></div><span class="event-flag">'+
      safe(describe(summary.status))+'</span></div>'+
      '<div class="event-mode-picker">'+modeItems(group).map(m=>'<button type="button" class="outline-button '+(m.id===summary.id?"mode-selected":"")+'" data-mode-id="'+safe(m.id)+'" aria-pressed="'+(m.id===summary.id)+'">'+safe(modeLabel(m))+' · '+safe(modeScore(m))+'</button>').join("")+'</div><h3 class="event-mode-title">'+safe(label)+'</h3><p class="watch-muted">Mode window: '+safe(dateRange(summary))+'</p>'+
      watchChart(caseFile.assessments)+
      '<div class="case-detail-numbers"><div><span>FIRST SEEN</span><strong>'+safe(stamp(caseFile.first_seen))+'</strong></div>'+
      '<div><span>LAST REPORTED</span><strong>'+safe(stamp(caseFile.last_seen))+'</strong></div>'+
      '<div><span>FORECAST CAPTURES</span><strong>'+safe(timeline.length)+'</strong></div></div>'+
      '<p class="case-detail-copy">We started tracking this when a daily forecast crossed our '+(unit==="signal"?"matching daily weather-code":safe(trigger)+safe(summary.threshold?.value)+' '+safe(unit))+' screening level. '+
      safe(modelEvidence(summary))+". "+
      safe(localEvidence(summary))+'</p>'+
      '<p class="case-detail-copy">Observation verification is shown separately when available. A captured forecast is not a storm observation, and this signal is not an NWS watch, warning, or calibrated risk forecast.</p>'+
      (assessments.length?'<div class="capture-scroll"><table class="capture-table"><thead><tr><th>Reassessed</th><th>Support</th><th>Unknown models</th><th>Model status</th><th>Forecast days</th></tr></thead><tbody>'+assessments.map(t=>timelineRow(t,summary.kind)).join("")+'</tbody></table></div>':'')+
      '<div class="capture-scroll"><table class="capture-table"><thead><tr><th>Positive capture</th><th>Models</th><th>Same-place support</th><th>Strongest daily sample*</th><th>Forecast days</th></tr></thead><tbody>'+
      timeline.slice(0,25).map(t=>timelineRow(t,summary.kind)).join("")+'</tbody></table></div>'+
      '<p class="fine-print">*The strongest triggering model/day/location sample is not a forecast for your selected town. '+
      'Values represent archived stitched guidance, not model initialization times. See the separate verification panel for any available measured evidence.'+
      (timeline.length>25?" Showing 25 most recent of "+timeline.length+" captures.":"")+'</p>';
    q("eventCaseList").querySelectorAll(".case-choice").forEach(button=>
      button.classList.toggle("is-selected",group?.mode_ids.includes(button.dataset.caseId)||button.dataset.caseId===summary.id));
  }
  async function reload(force=false){
    if(state.inFlight)return state.inFlight;
    if(!force&&state.index&&Date.now()-state.loadedAt<5*60*1000){
      renderHorizon();renderArchiveList();return;
    }
    state.inFlight=read("index.json").then(d=>{
      state.index=d;state.catalog=null;state.loadedAt=Date.now();
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
    q("eventCaseDetail")?.addEventListener("click",ev=>{
      const button=ev.target.closest("[data-mode-id]");if(!button)return;
      state.selected=button.dataset.modeId;renderArchiveList();
    });
    q("eventCaseList")?.addEventListener("click",ev=>{
      const button=ev.target.closest("[data-case-id]");if(!button)return;
      state.selected=button.dataset.caseId;renderArchiveList();
    });
    document.querySelectorAll('[data-tab="now"],[data-tab="archive"]').forEach(button=>
      button.addEventListener("click",()=>reload()));
    document.addEventListener("visibilitychange",()=>{
      if(document.visibilityState==="visible")reload();
    });
    q("refresh")?.addEventListener("click",()=>reload(true));
    reload();
    window.setInterval(()=>{if(document.visibilityState==="visible")reload(true)},15*60*1000);
  }
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});
  else init();
})();
