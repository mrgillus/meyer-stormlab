/* Meyer StormLab narrative preview: read-only client, no changes to core pipelines. */
(function(){
"use strict";
const RAW="https://raw.githubusercontent.com/mrgillus/meyer-stormlab/main/data/";
const TZ="America/Chicago";
const LOCS={
  "Wentzville":{lat:38.8114,lon:-90.8529},
  "Lake St. Louis":{lat:38.7976,lon:-90.7857},
  "Florissant":{lat:38.7892,lon:-90.3226},
  "Troy":{lat:38.9795,lon:-90.9807},
  "Hawk Point":{lat:38.9709,lon:-91.1335}
};
const MODEL_ORDER=["GFS","ECMWF","GEM","ICON","HRRR","RRFS","NBM"];
const EXT_MODELS=[
  {id:"ncep_gfs_global",name:"GFS",days:16},
  {id:"ecmwf_ifs025",name:"ECMWF",days:10},
  {id:"cmc_gem_gdps",name:"GEM",days:10},
  {id:"icon_global",name:"ICON",days:7}
];
const q=id=>document.getElementById(id);
const state={report:null,status:null,history:null,tab:"now",horizon:null,location:"Wentzville",archiveLoading:false};
const num=x=>typeof x==="number" && Number.isFinite(x);
const inches=x=>num(x)?x.toFixed(2)+'"':"—";
const shortDate=d=>new Intl.DateTimeFormat("en-US",{timeZone:TZ,month:"short",day:"numeric"}).format(new Date(d));
const ct=d=>new Intl.DateTimeFormat("en-US",{timeZone:TZ,month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"}).format(new Date(d));
const hour=d=>new Intl.DateTimeFormat("en-US",{timeZone:TZ,weekday:"short",hour:"numeric"}).format(new Date(d));
const e=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const set=(id,s)=>{if(q(id))q(id).textContent=s};
const stamp=()=>String(Date.now());
async function read(path){
  const u=RAW+path+"?v="+stamp();
  const r=await fetch(u,{cache:"no-store",signal:AbortSignal.timeout(20000)});
  if(!r.ok)throw Error("Published data returned HTTP "+r.status);
  return r.json();
}
function storedLocation(){
  try{const v=window.localStorage.getItem("stormlab.preview.location");if(LOCS[v])return v}catch(_){}return "Wentzville";
}
function selected(){return state.report?.points?.find(p=>p.kind==="town"&&p.name===state.location)||null}
function statusMode(d){
  if(!d)return "unknown";
  const now=Date.now(),start=Date.parse(d.window_start),end=Date.parse(d.window_end);
  if(!Number.isFinite(start)||!Number.isFinite(end))return "unknown";
  return now>end?"past":now>=start?"active":"future";
}
function goTab(tab,updateHash=true){
  if(!["now","horizon","archive","lab"].includes(tab))tab="now";
  state.tab=tab;
  document.querySelectorAll(".nav-item").forEach(b=>{
    const on=b.dataset.tab===tab;b.classList.toggle("active",on);
    if(on)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
  });
  ["now","horizon","archive","lab"].forEach(t=>{
    const el=q("view-"+t);el.hidden=t!==tab;el.classList.toggle("active",t===tab);
  });
  if(updateHash && window.location.hash!=="#"+tab)history.replaceState(null,"","#"+tab);
  if(tab==="lab")loadLab();
  if(tab==="archive")renderCase();
  if(tab==="horizon"&&!state.horizon)loadHorizon();
  window.scrollTo({top:0,behavior:"instant"});
}
function updateHealth(){
  const dot=q("healthDot"),d=state.report, s=state.status;
  dot.className="live-dot";
  if(!d){set("lastUpdate","Forecast unavailable");return}
  const age=(Date.now()-Date.parse(d.retrieved_at))/3600000;
  const statusFailed=s&&s.status!=="published"&&Date.parse(s.retrieved_at)>=Date.parse(d.retrieved_at);
  dot.classList.add(age<=2&&!statusFailed?"good":"old");
  set("lastUpdate",(age>2?"Stale data · ":"Model update · ")+ct(d.retrieved_at));
  set("sourceAge",age>2?"DATA MAY BE STALE":"Latest published analysis");
}
function stormNarrative(p,d,mode){
  if(mode==="past")return "The forecast has ended. This is the archived model outlook, not a measurement of what accumulated. A verified storm recap is still being built.";
  const v=p.blend_in, remain=p.remaining_forecast_in;
  const models=Object.values(p.models||{}).filter(m=>m.hours>0).length;
  if(num(v)&&num(remain)){
    return "Our "+models+" contributing sources put this event near "+v.toFixed(1)+' inches in the modeled storm window. '+remain.toFixed(1)+" inches were forecast for the remaining hours at the last update. The final ground total could be different.";
  }
  if(num(v))return "StormLab's experimental seven-source snowfall blend indicates "+v.toFixed(1)+" inches across the event window. Check the model coverage before treating this as a complete storm forecast.";
  return "Published snowfall guidance is incomplete for this location. We won't fill missing model hours with invented totals.";
}
function renderNow(){
  const d=state.report,p=selected();
  if(!d||!p){
    set("heroNarrative","The current StormLab model report could not be retrieved. The original dashboard remains available.");
    set("modelNote","No confirmed storm forecast is available right now. Please retry in a moment.");
    q("modelNote").classList.add("warning");
    updateHealth();return;
  }
  const mode=statusMode(d);
  const stampAge=(Date.now()-Date.parse(d.retrieved_at))/3600000;
  const hourData=Array.isArray(p.hourly)?p.hourly:[];
  const future=hourData.filter(h=>Date.parse(h.end_ct)>Date.now()&&num(h.snow_in));
  const comparison=(future.length?future:hourData.filter(h=>num(h.snow_in)));
  const peak=comparison.reduce((a,b)=>(!a||b.snow_in>a.snow_in?b:a),null);
  const sources=Object.values(p.models||{}).filter(m=>m?.hours>0).length;
  set("stormMode",mode==="past"?"FORECAST ARCHIVED":mode==="active"?"STORM IN PROGRESS":"PATTERN MONITORING");
  q("stormMode").classList.toggle("ended",mode==="past");
  const start=shortDate(d.window_start),end=shortDate(new Date(Date.parse(d.window_end)-3600000));
  set("eventDates",start+"–"+end+" · Greater St. Louis");
  set("heroHeadline",mode==="past"?"the forecast remembered.":mode==="active"?"is in motion.":"is taking shape.");
  set("heroNarrative",stormNarrative(p,d,mode));
  set("totalLabel",mode==="past"?"ARCHIVED MODEL TOTAL":"MODELED STORM WINDOW");
  q("bigSnow").innerHTML=num(p.blend_in)?e(p.blend_in.toFixed(2))+'<small> in</small>':'—<small> in</small>';
  set("snowFootnote",mode==="past"?"Not verified storm accumulation":"Forecast, not observed snow depth");
  set("peakTime",peak?hour(peak.end_ct):"—");
  set("peakValue",peak?(peak.snow_in.toFixed(2)+'" modeled per hour'):"No complete hourly forecast");
  set("modelCount",sources?String(sources):"—");
  const blocked=state.status?.status!=="published" && state.status && Date.parse(state.status.retrieved_at)>=Date.parse(d.retrieved_at);
  const pieces=[
    "Captured "+ct(d.retrieved_at),
    sources+" contributing model sources",
    d.errors?.length?d.errors.length+" retrieval errors":"No report retrieval errors"
  ];
  if(stampAge>2)pieces.unshift("CAUTION: forecast capture older than 2 hours");
  if(blocked)pieces.push("More recent attempt incomplete; retained prior successful forecast");
  set("modelNote",pieces.join(" · "));
  q("modelNote").classList.toggle("warning",stampAge>2||Boolean(blocked));
  updateHealth();
  renderPulse(p,mode);
  renderInsights(p,mode,peak);
  renderModels(p,d);
  renderCase();
}
function renderPulse(p,mode){
  const all=(p.hourly||[]).filter(x=>num(x.snow_in));
  const now=Date.now();
  let bars=all;
  if(mode==="active"){
    const ix=all.findIndex(x=>Date.parse(x.end_ct)>now);
    if(ix>=0)bars=all.slice(Math.max(0,ix-7),Math.min(all.length,ix+22));
  }else if(mode==="future")bars=all.slice(0,36);
  else if(all.length>40)bars=all.filter((_,i)=>i%2===0);
  if(!bars.length){
    q("pulseChart").innerHTML='<div class="empty-pulse">No complete hourly guidance for this location.</div>';
    set("chartHelp","Awaiting a complete forecast sequence.");
    return;
  }
  const peak=Math.max(.05,...bars.map(x=>x.snow_in));
  q("pulseChart").innerHTML=bars.map(h=>{
    const pct=Math.max(1,Math.min(100,h.snow_in/peak*100));
    const isPeak=h.snow_in>=peak-.00001;
    const classes="pulse-bar"+(Date.parse(h.end_ct)<=now?" past":"")+(isPeak?" peak":"");
    const label=hour(h.end_ct)+" · "+h.snow_in.toFixed(3)+' inches modeled'+(h.source==="prior_forecast"?" · frozen earlier forecast, not observed":"");
    return '<button type="button" class="'+classes+'" style="height:'+pct.toFixed(1)+'%" title="'+e(label)+'" aria-label="'+e(label)+'"></button>';
  }).join("");
  const start=bars[0].end_ct,end=bars[bars.length-1].end_ct;
  q("pulseAxis").innerHTML="<span>"+e(hour(start))+"</span><span>"+e(mode==="active"?"Past ← · → Forecast":"Modeled storm window")+"</span><span>"+e(hour(end))+"</span>";
  set("chartHelp",mode==="active"?"The storm's modeled hourly heartbeat; dimmed bars are frozen older forecasts.":mode==="past"?"An archived forecast timeline, not a reconstructed observation.":"A developing forecast timeline; peak timing may change.");
  set("pulseProvenance","Hourly snowfall model estimates. Forecast-hours already elapsed are preserved from previous captures—not measured snowfall.");
}
function renderInsights(p,mode,peak){
  const models=MODEL_ORDER.map(n=>({name:n,...(p.models?.[n]||{})})).filter(x=>x.hours>0&&num(x.covered_total_in??x.full_total_in));
  const comparable=models.filter(x=>x.hours===models[0]?.hours);
  const vals=comparable.map(x=>x.full_total_in??x.covered_total_in);
  const min=vals.length?Math.min(...vals):null,max=vals.length?Math.max(...vals):null;
  const tips=[];
  if(peak)tips.push(["Peak timing",peak.snow_in.toFixed(2)+'" per hour near '+hour(peak.end_ct)+". Timing comes from the latest published blended forecast."]);
  if(min!==null&&max!==null&&comparable.length>=2){
    const range=(max-min).toFixed(2);
    tips.push(["A range of perspectives",comparable.length+" models covering the same "+comparable[0].hours+" forecast hours span "+min.toFixed(1)+'" to '+max.toFixed(1)+'" ('+range+'" spread).']);
  }else tips.push(["Incomplete comparison","Not enough models cover matching forecast hours to compute a defensible model spread."]);
  if(mode==="past")tips.push(["Awaiting ground truth","The archive contains predictions, not independently verified actual snowfall. A final performance verdict is pending."]);
  else if(num(p.remaining_forecast_in))tips.push(["The next chapter",p.remaining_forecast_in.toFixed(2)+'" of modeled snowfall lies in the future hours of this report. This is not a measurement or a nowcast.']);
  else tips.push(["The coverage question","Some forecast hours are unavailable. StormLab does not treat missing data as zero."]);
  q("insights").innerHTML=tips.map((x,i)=>'<div class="insight"><span class="insight-marker" aria-hidden="true">'+["◎","⌁","✳"][i]+'</span><div><strong>'+e(x[0])+'</strong><p>'+e(x[1])+'</p></div></div>').join("");
}
function renderModels(p,d){
  const all=MODEL_ORDER.map(name=>({name,...(p.models?.[name]||{})}));
  const active=all.filter(m=>m.hours>0&&num(m.full_total_in??m.covered_total_in));
  const max=Math.max(1,...active.map(m=>m.full_total_in??m.covered_total_in));
  const h=new Set(active.map(m=>m.hours));
  set("modelWindow",active.length+" sources · "+(h.size===1?active[0].hours+" hours of matching coverage":Array.from(h).sort((a,b)=>a-b).join(", ")+" hours of varying coverage")+". The main blend spans "+d.hours+" hours and may include frozen earlier forecast hours.");
  if(!active.length){q("modelBars").innerHTML='<p class="muted">No model data returned for this location.</p>';return}
  q("modelBars").innerHTML=active.map(m=>{
    const v=m.full_total_in??m.covered_total_in;
    return '<button type="button" class="model-row" data-model="'+e(m.name)+'" title="'+e(m.name+": "+v.toFixed(2)+' inches, '+m.hours+" hours of future model coverage")+'"><span class="model-name">'+e(m.name)+'</span><div class="model-track"><div class="model-fill" style="width:'+Math.max(.5,100*v/max).toFixed(1)+'%"></div></div><span class="model-val">'+e(v.toFixed(2))+'"<small> '+e(m.hours)+"h</small></span></button>";
  }).join("");
  q("modelBars").querySelectorAll(".model-row").forEach(b=>b.addEventListener("click",()=>selectModel(b.dataset.model)));
  selectModel(active.some(x=>x.name==="HRRR")?"HRRR":active[0].name);
}

function selectModel(name){
  const p=selected(),m=p?.models?.[name];if(!m)return;
  const v=m.full_total_in??m.covered_total_in;if(!num(v))return;
  q("modelBars").querySelectorAll(".model-row").forEach(b=>b.classList.toggle("is-selected",b.dataset.model===name));
  const details=[];
  if(num(m.peak_hour_in))details.push("Peak modeled hour: "+m.peak_hour_in.toFixed(2)+'"');
  if(num(m.snow_hour_mean_temp_c))details.push("Mean snow-hour air temp: "+m.snow_hour_mean_temp_c.toFixed(1)+"°C");
  if(num(m.snow_hour_mean_wetbulb_c))details.push("Mean snow-hour wet bulb: "+m.snow_hour_mean_wetbulb_c.toFixed(1)+"°C");
  q("modelDetail").innerHTML='<strong>'+e(name)+' · '+e(v.toFixed(2))+'" modeled snow</strong>'+
    '<span>'+e(m.hours+" forecast hours represented. "+(m.full_total_in===null?"Only future hours are available in the current capture.":"Full storm window represented.")+" The model initialization cycle is not recorded by this feed.")+'</span>'+
    (details.length?'<div class="detail-metrics">'+details.map(v=>'<span>'+e(v)+'</span>').join("")+'</div>':"");
}
async function loadNow(silent=false){
  if(!silent)set("lastUpdate","Checking latest model capture…");
  const results=await Promise.allSettled([read("storm/report.json"),read("storm/status.json")]);
  if(results[0].status==="fulfilled"){
    state.report=results[0].value;
    state.status=results[1].status==="fulfilled"?results[1].value:null;
  }else if(!state.report){
    set("lastUpdate","StormLab feed unavailable");
    set("modelNote","Could not load the published storm model data: "+results[0].reason.message);
  }
  renderNow();
}
function horizonInputs(m){
  const loc=LOCS[state.location];
  const params=new URLSearchParams({
    latitude:String(loc.lat),longitude:String(loc.lon),models:m.id,
    daily:"snowfall_sum,precipitation_sum,wind_gusts_10m_max,temperature_2m_min,temperature_2m_max",
    timezone:TZ,temperature_unit:"fahrenheit",wind_speed_unit:"mph",
    precipitation_unit:"inch",forecast_days:String(m.days)
  });
  return "https://api.open-meteo.com/v1/forecast?"+params.toString();
}
function classify(v){
  const s=[];
  if(num(v.snow)&&v.snow>=2)s.push({kind:"Snow potential",value:v.snow,unit:'" snow'});
  if(num(v.rain)&&v.rain>=.9)s.push({kind:"Heavy precipitation signal",value:v.rain,unit:'" liquid'});
  if(num(v.gust)&&v.gust>=40)s.push({kind:"Strong wind signal",value:v.gust,unit:" mph gust"});
  if(num(v.low)&&v.low<=10)s.push({kind:"Notable cold",value:v.low,unit:"°F minimum"});
  if(num(v.high)&&v.high>=98)s.push({kind:"Extreme heat",value:v.high,unit:"°F maximum"});
  return s;
}
function datePlus(date,offset){const d=new Date(date+"T12:00:00Z");d.setUTCDate(d.getUTCDate()+offset);return d.toISOString().slice(0,10)}
async function loadHorizon(){
  const button=q("horizonRefresh");button.disabled=true;
  set("horizonStatus","Scanning model guidance…");
  q("watchList").innerHTML='<div class="blank-state">Checking the latest available daily model forecasts…</div>';
  const futures=EXT_MODELS.map(async m=>{
    const r=await fetch(horizonInputs(m),{cache:"no-store",signal:AbortSignal.timeout(18000)});
    if(!r.ok)throw Error(m.name+" HTTP "+r.status);
    const j=await r.json();if(j.error||!j.daily?.time)throw Error(m.name+" returned no daily guidance");
    return {model:m.name,daily:j.daily};
  });
  const settled=await Promise.allSettled(futures);
  const feeds=settled.filter(x=>x.status==="fulfilled").map(x=>x.value);
  state.horizon={feeds,updated:new Date(),failed:settled.length-feeds.length};
  renderHorizon();
  button.disabled=false;
}
function renderHorizon(){
  const d=state.horizon;if(!d)return;
  const feeds=d.feeds;set("horizonStatus",feeds.length+"/"+EXT_MODELS.length+" feeds · "+new Intl.DateTimeFormat("en-US",{timeZone:TZ,hour:"numeric",minute:"2-digit"}).format(d.updated));
  if(!feeds.length){
    q("watchList").innerHTML='<div class="blank-state"><span class="notice-error">The extended model feeds could not be reached.</span> There is no reliable 16-day outlook to display. Try again later. The Now view still works independently.</div>';return;
  }
  const parts=new Intl.DateTimeFormat("en-US",{timeZone:TZ,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
  const getPart=type=>parts.find(part=>part.type===type)?.value;
  const today=getPart("year")+"-"+getPart("month")+"-"+getPart("day");
  const records=[];
  for(const f of feeds){
    for(let i=0;i<f.daily.time.length;i++){
      const date=f.daily.time[i],lead=Math.round((Date.parse(date+"T12:00:00Z")-Date.parse(today+"T12:00:00Z"))/86400000);
      if(lead<3||lead>16)continue;
      const v={snow:f.daily.snowfall_sum?.[i],rain:f.daily.precipitation_sum?.[i],
        gust:f.daily.wind_gusts_10m_max?.[i],low:f.daily.temperature_2m_min?.[i],high:f.daily.temperature_2m_max?.[i]};
      for(const signal of classify(v))records.push({date,lead,model:f.model,...signal});
    }
  }
  records.sort((a,b)=>a.date.localeCompare(b.date)||a.kind.localeCompare(b.kind));
  const groups=[];
  for(const r of records){
    const prev=groups.find(g=>g.kind===r.kind&&Math.abs(g.lastLead-r.lead)<=1);
    if(prev){
      prev.lastLead=Math.max(prev.lastLead,r.lead);prev.firstLead=Math.min(prev.firstLead,r.lead);
      prev.models.add(r.model);prev.max=Math.max(prev.max,r.value);prev.min=Math.min(prev.min,r.value);
    }else groups.push({kind:r.kind,firstLead:r.lead,lastLead:r.lead,models:new Set([r.model]),max:r.value,min:r.value,unit:r.unit});
  }
  groups.sort((a,b)=>b.models.size-a.models.size||a.firstLead-b.firstLead);
  const featured=groups.slice(0,6);
  set("horizonWindow","Day 3–16 · model availability varies");
  if(!featured.length){
    q("watchList").innerHTML='<div class="blank-state"><strong>No strong signals in the daily fields we could retrieve.</strong><p>That does not mean the next two weeks are risk-free. These checks cannot diagnose severe thunderstorms or flooding, and only '+e(feeds.length)+' long-range feeds responded.</p></div>';return;
  }
  q("watchList").innerHTML=featured.map(g=>{
    const corroborated=g.models.size>=2;
    const start=datePlus(today,g.firstLead),end=datePlus(today,g.lastLead);
    const dates=shortDate(start)+(g.firstLead===g.lastLead?"":" – "+shortDate(end));
    const level=corroborated?"Multiple-model signal":"Single-model signal";
    const models=[...g.models].join(" · ");
    const disclaimer=corroborated?"More than one independent model shows this broad signal; exact outcomes remain uncertain.":"Only one source reaches the screening threshold. Treat as low-confidence and unconfirmed.";
    return '<article class="watch-card"><div class="watch-meta"><span class="kicker">'+e(dates)+'</span><span class="watch-level '+(corroborated?"multiple":"")+'">'+e(level)+'</span></div><h3>'+e(g.kind)+'</h3><p>'+e(disclaimer)+'</p><div class="watch-facts"><span>'+e(models)+'</span><span>Strongest modeled value: '+e(g.max.toFixed(g.unit.includes("mph")||g.unit.includes("°F")?0:2)+g.unit)+'</span><span>Day '+e(g.firstLead)+(g.lastLead>g.firstLead?"–"+e(g.lastLead):"")+'</span></div></article>';
  }).join("");
}
function renderCase(){
  if(!state.report)return;
  const p=selected();if(!p)return;
  set("caseModel",inches(p.blend_in));
  set("caseObserved","Not connected");
  const mode=statusMode(state.report);
  set("caseStatus",mode==="past"?"Verification pending":"Forecasting underway");
  set("caseSummary",mode==="past"?
    "The storm forecast window has ended. We can revisit historical model predictions, but independently verified snowfall amounts and a model-accuracy verdict are not yet available.":
    "This storm is still within its forecast window. These are forecast predictions, not final measured accumulations. The post-storm verification step is pending.");
  if(state.history)renderCaseHistory();
}
async function loadCase(){
  if(state.archiveLoading)return;
  const button=q("loadCase");state.archiveLoading=true;button.disabled=true;
  set("caseCaptures","Loading…");
  try{state.history=await read("history.json");if(!Array.isArray(state.history))throw Error("Unexpected forecast archive format");renderCaseHistory()}
  catch(err){set("caseCaptures","Unavailable");q("caseTimeline").innerHTML='<p class="notice-error">Could not load the forecast snapshots: '+e(err.message)+'</p>'}
  finally{state.archiveLoading=false;button.disabled=false}
}
function renderCaseHistory(){
  if(!state.history)return;
  const rows=[];
  for(const capture of state.history){
    const location=capture.locations?.[state.location];if(!location)continue;
    const mods={};let filled=0;
    for(const name of ["GFS","ECMWF","GEM","ICON"]){
      const model=location[name],data=model?.daily;
      if(!data?.time){mods[name]=null;continue}
      let sum=0,valid=0;
      for(const day of ["2026-11-25","2026-11-26"]){
        const i=data.time.indexOf(day),v=i>=0?data.snowfall_sum?.[i]:null;
        if(!num(v))continue;
        const unit=model.units?.snowfall_sum;
        sum+=unit==="cm"?v/2.54:v;valid++;
      }
      mods[name]=valid===2?sum:null;if(valid===2)filled++;
    }
    if(filled)rows.push({stamp:capture.retrieved_at,mods});
  }
  rows.sort((a,b)=>a.stamp.localeCompare(b.stamp));
  set("caseCaptures",rows.length+" usable captures");
  if(!rows.length){
    q("caseTimeline").innerHTML='<p class="muted">No complete two-day storm totals were found in the published forecast-snapshot archive for this location.</p>';return;
  }
  const shown=rows.slice(-10).reverse();
  const headings=["Captured (CT)","GFS","ECMWF","GEM","ICON"];
  q("caseTimeline").innerHTML='<div class="capture-scroll"><table class="capture-table"><thead><tr>'+headings.map(h=>'<th scope="col">'+e(h)+'</th>').join("")+'</tr></thead><tbody>'+
    shown.map(r=>'<tr><td>'+e(ct(r.stamp))+'</td>'+headings.slice(1).map(m=>'<td>'+(num(r.mods[m])?e(r.mods[m].toFixed(2))+'"':'—')+'</td>').join("")+'</tr>').join("")+'</tbody></table></div>'+
    '<p class="fine-print">Last 10 usable published guidance snapshots, shown newest first. Each value totals the November 25–26 daily snowfall guidance available at capture time. Snapshots can stitch forecast cycles and are not independent model initialization times. These are forecasts, not observations.</p>';
}
async function loadLab(){
  const results=await Promise.allSettled([read("gefs_archive/progress.json"),read("gefs48/progress.json")]);
  function show(id,idLabel,data,key){
    if(!data){set(idLabel,"Unavailable");q(id).style.width="0%";return}
    const n=data[key]||0,total=data.total_initializations||0,pct=total?100*n/total:0;
    set(idLabel,n.toLocaleString()+"/"+total.toLocaleString()+" · "+pct.toFixed(1)+"%");
    q(id).style.width=Math.min(100,pct)+"%";
  }
  const a=results[0].status==="fulfilled"?results[0].value:null;
  const b=results[1].status==="fulfilled"?results[1].value:null;
  show("archiveCore","archiveCoreLabel",a,"core_complete");
  show("archive48","archive48Label",b,"complete");
  set("archiveUpdated",a||b?"Latest checkpoints · "+[a?.generated_at,b?.generated_at].filter(Boolean).map(ct).join(" / ")+" · A completed archive record is not the same as a validated training example.":"Published archive checkpoints unavailable.");
}
function install(){
  state.location=storedLocation();q("location").value=state.location;
  q("location").addEventListener("change",ev=>{
    if(!LOCS[ev.target.value])return;
    state.location=ev.target.value;
    try{window.localStorage.setItem("stormlab.preview.location",state.location)}catch(_){}
    renderNow();
    if(state.tab==="lab")loadLab();
    if(state.tab==="horizon"){state.horizon=null;loadHorizon()}
    if(state.tab==="archive")renderCase();
  });
  document.querySelectorAll("[data-tab]").forEach(button=>button.addEventListener("click",()=>goTab(button.dataset.tab)));
  document.querySelectorAll("[data-go]").forEach(button=>button.addEventListener("click",()=>goTab(button.dataset.go)));
  q("showEvidence").addEventListener("click",()=>q("evidence").scrollIntoView({behavior:"smooth",block:"start"}));
  q("refresh").addEventListener("click",async()=>{
    q("refresh").disabled=true;
    try{await loadNow();if(state.tab==="lab")await loadLab();if(state.tab==="horizon")await loadHorizon();if(state.tab==="archive"&&state.history)renderCaseHistory()}
    finally{q("refresh").disabled=false}
  });
  q("horizonRefresh").addEventListener("click",loadHorizon);
  q("loadCase").addEventListener("click",loadCase);
  window.addEventListener("hashchange",()=>goTab(location.hash.replace("#",""),false));
  goTab(location.hash.replace("#","")||"now",false);
  loadNow();
  window.setInterval(()=>{if(document.visibilityState==="visible")loadNow(true)},10*60*1000);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",install,{once:true});else install();
})();