/* StormLab real station evidence, explicitly unscored until clock alignment. */
(function(){
"use strict";
const API="https://raw.githubusercontent.com/mrgillus/meyer-stormlab/main/data/verification/report.json";
const q=id=>document.getElementById(id);
const safe=x=>String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtTime=s=>new Intl.DateTimeFormat("en-US",{timeZone:"America/Chicago",month:"short",day:"numeric",hour:"numeric",minute:"2-digit",timeZoneName:"short"}).format(new Date(s));
const money=x=>typeof x==="number"&&Number.isFinite(x)?x.toFixed(2)+'"':"—";
const day=s=>new Intl.DateTimeFormat("en-US",{timeZone:"America/Chicago",month:"short",day:"numeric"}).format(new Date(s+"T12:00:00Z"));
const state={report:null,promise:null,chosen:null,updated:0};
function set(id,s){if(q(id))q(id).textContent=s}
function noData(message){
 set("verificationBadge","OBSERVATIONS PENDING");
 q("verificationContent").innerHTML='<div class="blank-state">'+safe(message)+'</div>';
}
function show(eventId){
 state.chosen=eventId;
 if(!q("verificationContent"))return;
 const report=state.report;
 if(!report){
   noData("Waiting for independent station-observation data. We'll show only reported measurements, never invented totals.");
   return;
 }
 const event=report.events?.find(x=>x.event_id===eventId);
 if(!event){
   noData("No independent station-verification record has been generated for this event yet.");
   return;
 }
 const rows=Array.isArray(event.station_daily_reports)?event.station_daily_reports:[];
 const future=event.observation_status==="event_window_not_complete";
 const available=rows.length>0;
 const label=available?(future?"EARLY STATION REPORTS":"STATION REPORTS AVAILABLE"):"AWAITING MEASUREMENTS";
 set("verificationBadge",label);
 const intro=available?
   "These are actual daily station reports from RCC-ACIS, for stations near towns in our forecast area. They are not the verified total snowfall from the storm and may not align with the event's midnight-to-midnight model windows.":
   "No compatible measured station reports have been linked to this event yet. Some stations report after the event, and the climate-data archive may update later.";
 let body='<div class="verification-note"><span class="verification-symbol" aria-hidden="true">'+(available?"◎":"⌛")+'</span><div>'+
   '<strong>'+safe(available?"Measurements found; verification still pending.":"Ground truth is still being assembled.")+'</strong>'+
   '<p>'+safe(intro)+'</p></div></div>';
 body+='<div class="verification-summary"><div><span>INDEPENDENT STATIONS</span><strong>'+safe(event.station_count||0)+'</strong></div>'+
   '<div><span>MEASURED DAILY REPORTS</span><strong>'+safe(event.daily_report_count||0)+'</strong></div>'+
   '<div><span>MODEL VERIFICATION</span><strong>'+(event.event_total_verification?.status==="scored"?"Event total scored":"Not scored")+'</strong></div></div>';
 if(available){
  body+='<div class="capture-scroll"><table class="capture-table"><thead><tr><th>Reporting station</th>'+
    '<th>Reported date</th><th>Daily measurement</th><th>Near town</th></tr></thead><tbody>'+
    rows.slice(0,24).map(r=>'<tr><td>'+safe(r.station_name)+'</td><td>'+safe(day(r.reported_date))+'</td>'+
      '<td>'+safe(money(r.value_in))+' '+safe(r.quantity)+'</td>'+
      '<td>'+safe(r.near_town)+' ('+safe(r.distance_to_near_town_mi)+' mi)</td></tr>').join("")+
      '</tbody></table></div>';
  if(rows.length>24)body+='<p class="fine-print">Showing the first 24 of '+safe(rows.length)+' available daily station reports.</p>';
 }
 const tot=event.event_total_verification;
 if(tot?.status==="scored"){
   const w=tot.verification_window,unit=tot.variable==="snow"?"snowfall":"liquid precipitation";
   body+='<div class="verification-forecast"><span class="kicker">EVENT-TOTAL VERIFICATION · '+safe(unit.toUpperCase())+'</span>'+
     '<p>Forecast captured '+safe(fmtTime(tot.forecast_capture.captured_at))+' for '+safe(day(w.start))+'–'+safe(day(w.end))+
     ', compared with station reports dated '+safe(day(w.observed_report_dates.start))+'–'+safe(day(w.observed_report_dates.end))+
     '. That span covers the forecast window whatever time each station observes, so no observation clock is assumed. '+safe(tot.stations_scored)+' stations within 10 miles of a forecast town.</p>'+
     '<div class="capture-scroll"><table class="capture-table"><thead><tr><th>Model</th><th>Stations</th><th>Mean abs. error</th><th>Bias</th></tr></thead><tbody>'+
     Object.entries(tot.scores||{}).map(([m,v])=>'<tr><td>'+safe(m==="model_mean"?"4-model mean":m)+'</td><td>'+safe(v.n)+'</td><td>'+safe(money(v.mae_in))+'</td><td>'+safe((v.bias_in>0?"+":"")+money(v.bias_in))+'</td></tr>').join("")+
     '</tbody></table></div><p class="fine-print">One event at correlated nearby stations: a record of this storm, not a skill estimate or calibrated probability. Positive bias means the forecast was too high.</p></div>';
 }else if(tot?.reason){
   body+='<p class="fine-print">Event-total verification not scored: '+safe(tot.reason.replace(/_/g," "))+'.</p>';
 }
 const ref=event.forecast_reference;
 if(ref?.status==="pre_event_capture" && Array.isArray(ref.daily_forecasts)){
   const forecast=ref.daily_forecasts.filter(r=>r.town==="Wentzville");
   body+='<div class="verification-forecast"><span class="kicker">FROZEN MODEL CONTEXT / WENTZVILLE</span>'+
     '<p>StormLab found a model forecast captured before the event began: '+safe(fmtTime(ref.captured_at))+'. '+
     'These are the original predicted calendar-day snowfall values. We are preserving them, not comparing them numerically to unmatched station reporting windows.</p>';
   if(forecast.length){
     body+='<div class="verification-forecast-chips">'+forecast.slice(0,10).map(f=>'<span>'+
       safe(f.model)+' · '+safe(day(f.calendar_date))+' · '+safe(money(f.modeled_daily_snow_in))+'</span>').join("")+'</div>';
   }
   body+='</div>';
 }
 body+='<p class="verification-integrity"><strong>Why no accuracy score?</strong> '+safe(event.verification_reason||"A verified reporting clock and matched spatial forecast are required before scoring.")+'</p>';
 const at=report.source_latest_observation_capture;
 body+='<p class="fine-print">Independent data: RCC-ACIS, '+safe(report.provider_status)+
   (at?" · Retrieved "+safe(fmtTime(at)):" · No confirmed retrieval yet")+
   '. No reported snow amount is treated as a forecast-town storm total.</p>';
 q("verificationContent").innerHTML=body;
}
async function load(force=false){
 if(state.promise)return state.promise;
 if(state.report&&!force&&Date.now()-state.updated<10*60*1000){show(state.chosen);return}
 state.promise=(async()=>{
   try{
     const response=await fetch(API+"?t="+Date.now(),{cache:"no-store",signal:AbortSignal.timeout(20000)});
     if(!response.ok)throw Error("HTTP "+response.status);
     const d=await response.json();
     if(d.schema!==1||!Array.isArray(d.events))throw Error("Invalid observation-verification schema");
     state.report=d;state.updated=Date.now();
     show(state.chosen);
   }catch(e){
     if(!state.report)noData("The independent station-data feed is not published or temporarily unavailable ("+e.message+"). The event forecast timeline remains available.");
   }
 })().finally(()=>{state.promise=null});
 return state.promise;
}
function render(id){state.chosen=id;show(id);if(!state.report)load()}
function init(){
 if(!q("verificationContent"))return;
 document.querySelector('[data-tab="archive"]')?.addEventListener("click",()=>load());
 document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")load()});
 load();
}
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();
window.StormLabVerification={render,load};
})();