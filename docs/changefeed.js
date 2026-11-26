/* Meyer StormLab — device-local forecast-change briefing.
 * Forecast timestamps are capture times, not model initialization times.
 * Only matched upcoming forecast hours with identical contributing-source
 * availability are compared; no prior forecast is treated as observation.
 */
(function(){
"use strict";
const VERSION=2;
const PREFIX="stormlab.observatory.visit.v"+VERSION+":";
const MIN_MATCHED=6;
const MAX_AGE_MS=4*3600000;
const baseline=new Map();
const latest=new Map();
const q=id=>document.getElementById(id);
const number=x=>typeof x==="number"&&Number.isFinite(x);
const safe=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=n=>number(n)?n.toFixed(2):"—";
const abs=n=>Math.abs(n).toFixed(2);
const datestr=x=>new Intl.DateTimeFormat("en-US",{timeZone:"America/Chicago",month:"short",day:"numeric",hour:"numeric",minute:"2-digit"}).format(new Date(x))+" CT";
const key=location=>PREFIX+encodeURIComponent(location);
function readBaseline(location){
  if(baseline.has(location))return baseline.get(location);
  let old=null;
  try{
    const data=JSON.parse(window.localStorage.getItem(key(location))||"null");
    if(data&&data.version===VERSION&&data.location===location&&["storm","general"].includes(data.kind)&&
       Array.isArray(data.hours)&&data.hours.length<=192&&Number.isFinite(Date.parse(data.updated_at)) &&
       Number.isFinite(Date.parse(data.seen_at)))old=data;
  }catch(_){}
  baseline.set(location,old);
  return old;
}
function store(location,snapshot){
  try{
    // Never replace a newer captured forecast with older data after a failed fetch.
    const now=JSON.parse(window.localStorage.getItem(key(location))||"null");
    if(now?.version===VERSION && Date.parse(now.updated_at)>Date.parse(snapshot.updated_at))return false;
    window.localStorage.setItem(key(location),JSON.stringify(snapshot));
    return true;
  }catch(_){return false}
}
function asStorm(report,point,location){
  if(!report||!point||!Array.isArray(point.hourly))return null;
  const at=Date.parse(report.retrieved_at),end=Date.parse(report.window_end);
  if(!Number.isFinite(at)||!Number.isFinite(end))return null;
  const active=Object.entries(point.models||{}).filter(([,m])=>m?.hours>0).map(([name])=>name).sort();
  const hours=point.hourly.filter(h=>h.source==="current_forecast"&&
    number(h.snow_in)&&h.snow_in>=0&&Date.parse(h.end_ct)>0).map(h=>({
      t:Date.parse(h.end_ct),snow:h.snow_in,
      composition:["global","rapid","blend"].map(k=>Number(h.families?.[k]||0)).join("/")
    })).filter(h=>Number.isFinite(h.t)).slice(0,80);
  return {version:VERSION,kind:"storm",location,updated_at:report.retrieved_at,seen_at:new Date().toISOString(),
    event_id:report.window_start+"|"+report.window_end,sources:active,hours,
    issued_snow:number(point.blend_in)?point.blend_in:null,window_end:report.window_end};
}
function asGeneral(g,location){
  if(!g||g.location!==location||!Array.isArray(g.hours)||!Array.isArray(g.models))return null;
  const at=Date.parse(g.fetchedAt);
  if(!Number.isFinite(at)||!g.models.length)return null;
  const hours=g.hours.map(h=>({
    t:Date.parse(h.end_ct),snow:h.snow,liquid:h.liquid,gust:h.gust,
    composition:Number(h.sources||0)
  })).filter(h=>Number.isFinite(h.t)).slice(0,80);
  return {version:VERSION,kind:"general",location,updated_at:g.fetchedAt,seen_at:new Date().toISOString(),
    event_id:"rolling-48h",sources:g.models.map(m=>m.name).sort(),hours,
    issued_snow:number(g.snowTotal)?g.snowTotal:null};
}
function setBriefing(status,headline,body,facts,foot,icon,tone){
  const statusEl=q("briefingStatus"),h=q("briefingHeadline"),p=q("briefingSummary"),meta=q("briefingCompared"),mark=q("briefingMark");
  if(!statusEl||!h||!p||!meta||!mark)return;
  statusEl.textContent=status;h.textContent=headline;p.textContent=body;meta.textContent=foot;
  mark.textContent=icon||"✳";
  const card=q("visitBriefing");if(card)card.dataset.tone=tone||"neutral";
  const factsEl=q("briefingFacts");
  if(factsEl)factsEl.innerHTML=(facts||[]).map(v=>'<span>'+safe(v)+'</span>').join("");
}
function explain(old,current){
  if(!old){
    return {status:"BASELINE ESTABLISHED",headline:"The story starts here.",
      body:"We've saved the latest available forecast for this location in your browser. Come back after another model capture to see how its prediction changed.",
      facts:[current.sources.length+" model sources",current.hours.length+" forecast hours"],
      foot:"Saved on this device only · No historical comparison yet",icon:"✳",tone:"neutral"};
  }
  const prior=Date.parse(old.updated_at),now=Date.parse(current.updated_at);
  const seen=Number.isFinite(Date.parse(old.seen_at))?datestr(old.seen_at):"your previous visit";
  if(current.kind!==old.kind||current.event_id!==old.event_id){
    return {status:"A NEW CHAPTER",headline:"We're tracking a different forecast window.",
      body:"Your previous checkpoint covered "+(old.kind==="storm"?"a named storm":"a rolling 48-hour forecast")+
        ". The newest guidance covers "+(current.kind==="storm"?"a named storm":"a rolling 48-hour forecast")+
        ". We have started a new baseline instead of comparing unrelated periods.",
      facts:[current.sources.length+" current sources"],foot:"Previous checkpoint "+seen+" · No artificial trend calculated",icon:"↻",tone:"neutral"};
  }
  if(now<prior)return {status:"OLDER DATA",headline:"Waiting for a newer forecast capture.",
    body:"The published source is older than the checkpoint saved on this device. StormLab will not call that a new update.",
    facts:[],foot:"Last saved visit "+seen,icon:"⌛",tone:"neutral"};
  if(now===prior)return {status:"NO NEW CAPTURE",headline:"You're all caught up.",
    body:"The forecast capture hasn't changed since your last visit. When a newer data capture arrives, we'll compare matching hours.",
    facts:[current.sources.length+" model sources"],foot:"Your previous checkpoint "+seen,icon:"✓",tone:"neutral"};
  const a=old.sources.join("|"),b=current.sources.join("|");
  if(a!==b)return {status:"SOURCE MIX CHANGED",headline:"The model lineup has changed.",
    body:"The forecast now uses a different set of contributing model sources. A numerical change could reflect a missing or returning model, so we're not treating it as a forecast trend.",
    facts:["Previous: "+old.sources.join(", "),"Now: "+current.sources.join(", ")],
    foot:"New capture "+datestr(current.updated_at)+" · Comparison paused",icon:"⌁",tone:"neutral"};
  const oldHours=new Map(old.hours.map(h=>[h.t,h]));
  const matched=current.hours.filter(h=>{
    const was=oldHours.get(h.t);
    return h.t>Date.now()&&was&&was.composition===h.composition&&number(h.snow)&&number(was.snow);
  });
  if(matched.length<MIN_MATCHED)return {status:"LIMITED OVERLAP",headline:"A fresh capture, but no fair comparison yet.",
    body:"The two captures don't share enough future forecast hours with the same model-source coverage. StormLab won't compare totals over different time windows.",
    facts:[matched.length+" comparable future hours",current.sources.length+" sources"],
    foot:"New capture "+datestr(current.updated_at),icon:"⌁",tone:"neutral"};
  const sum=(arr,f)=>arr.reduce((a,x)=>a+f(x),0);
  const oldSnow=sum(matched,h=>oldHours.get(h.t).snow);
  const newSnow=sum(matched,h=>h.snow);
  const delta=newSnow-oldSnow;
  const first=matched[0].t,last=matched.at(-1).t;
  const hours=matched.length+" identical upcoming forecast hours";
  const facts=["Before "+fmt(oldSnow)+'"',"Now "+fmt(newSnow)+'"',matched.length+" matched hours"];
  const localTime=d=>new Intl.DateTimeFormat("en-US",{timeZone:"America/Chicago",hour:"numeric",weekday:"short"}).format(new Date(d));
  let peakNote="";
  if(current.kind==="storm"){
    const prevPeak=matched.reduce((a,b)=>oldHours.get(b.t).snow>oldHours.get(a.t).snow?b:a,matched[0]);
    const nextPeak=matched.reduce((a,b)=>b.snow>a.snow?b:a,matched[0]);
    if(Math.abs(nextPeak.t-prevPeak.t)>=3600000 && Math.max(nextPeak.snow,oldHours.get(prevPeak.t).snow)>=.1){
      peakNote=" The modeled peak shifted from "+localTime(prevPeak.t)+" to "+localTime(nextPeak.t)+".";
    }
  }
  const changeThreshold=Math.max(.12,.06*Math.max(oldSnow,newSnow));
  if(Math.abs(delta)>=changeThreshold){
    const rise=delta>0;
    return {status:"GUIDANCE "+(rise?"TRENDING HIGHER":"TRENDING LOWER"),
      headline:"Snowfall guidance "+(rise?"climbed":"eased")+" "+abs(delta)+'".',
      body:"Across "+hours+", the forecast moved from "+fmt(oldSnow)+'" to '+fmt(newSnow)+'" since your previous visit.'+peakNote,
      facts,foot:"Forecast-only comparison · New capture "+datestr(current.updated_at)+
        " · Captures may reuse model cycles",icon:rise?"↗":"↘",tone:rise?"rising":"falling"};
  }
  if(current.kind==="general"){
    const sameLiquid=matched.filter(h=>number(h.liquid)&&number(oldHours.get(h.t).liquid));
    if(sameLiquid.length>=MIN_MATCHED){
      const liquidDelta=sum(sameLiquid,h=>h.liquid-oldHours.get(h.t).liquid);
      if(Math.abs(liquidDelta)>=.2){
        return {status:"PRECIPITATION SHIFT",headline:"The liquid-precipitation outlook changed.",
          body:"Across "+sameLiquid.length+" matching future hours, forecast precipitation "+
            (liquidDelta>0?"rose":"fell")+" by "+abs(liquidDelta)+'". This is model liquid precipitation, not measured rainfall or flood risk.',
          facts:[(liquidDelta>0?"+":"−")+abs(liquidDelta)+'" liquid change',sameLiquid.length+" matched hours"],
          foot:"Forecast-only comparison · New capture "+datestr(current.updated_at),icon:liquidDelta>0?"↗":"↘",tone:"neutral"};
      }
    }
  }
  return {status:"GUIDANCE HOLDING STEADY",headline:"The models haven't moved much.",
    body:"The latest capture changed snowfall guidance by only "+abs(delta)+'" across '+hours+'.'+peakNote,
    facts,foot:"Forecast-only comparison · New capture "+datestr(current.updated_at),
    icon:"→",tone:"steady"};
}
function update(args){
  if(!args||!args.location||!q("visitBriefing"))return;
  const snapshot=args.kind==="storm"?asStorm(args.report,args.point,args.location):
    args.kind==="general"?asGeneral(args.general,args.location):null;
  if(!snapshot)return;
  latest.set(args.location,snapshot);
  const old=readBaseline(args.location);
  const age=Date.now()-Date.parse(snapshot.updated_at);
  if(!Number.isFinite(age)||age>MAX_AGE_MS||age<-(5*60*1000)){
    setBriefing("SOURCE NOT CURRENT","A fresh comparison is on hold.",
      "The last published forecast capture is too old or its clock is inconsistent. We won't present it as a new model update.",
      [],"Source captured "+datestr(snapshot.updated_at),"⌛","neutral");
    return;
  }
  if(!snapshot.sources.length||snapshot.hours.length<MIN_MATCHED){
    setBriefing("LIMITED DATA","There's not enough matching guidance.",
      "We'll resume change tracking when the source publishes enough usable forecast hours.",[],
      "Last capture "+datestr(snapshot.updated_at),"⌁","neutral");return;
  }
  const result=explain(old,snapshot);
  const persisted=store(args.location,snapshot);
  if(!old&&!persisted)result.foot="Local storage unavailable · Return-visit comparisons may not persist";
  setBriefing(result.status,result.headline,result.body,result.facts,result.foot,result.icon,result.tone);
}
function reset(){
  const sel=q("location")?.value;
  if(!sel||!latest.has(sel))return;
  baseline.set(sel,null);
  try{window.localStorage.removeItem(key(sel))}catch(_){}
  const snap=latest.get(sel),saved=store(sel,snap);
  setBriefing("BASELINE RESET","The next chapter starts now.",
    "This location's previous checkpoint was cleared. We'll compare future captured guidance with the forecast you're seeing now.",
    [snap.sources.length+" model sources",snap.hours.length+" forecast hours"],
    saved?"Saved on this device · Reset at "+datestr(new Date()):"Local storage unavailable",
    "✳","neutral");
}
function wire(){
  const resetButton=q("resetBriefing");
  if(resetButton)resetButton.addEventListener("click",reset);
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",wire,{once:true});else wire();
window.StormLabChanges={update};
})();