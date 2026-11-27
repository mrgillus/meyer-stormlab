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
    ["warm_day","cold_day","warm_night","cold_night"].includes(kind)?"σ":
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
  const families=[
    {id:"snow",name:"Snow and ice",icon:"❄",kinds:["snow","light_snow","ice"]},
    {id:"rain",name:"Rain and precipitation",icon:"☂",kinds:["rain","drizzle","showers","precipitation"]},
    {id:"severe",name:"Thunderstorms and severe weather",icon:"ϟ",kinds:["thunderstorm","tornado","hail","damaging_wind","severe"]},
    {id:"wind",name:"Wind",icon:"〰",kinds:["wind"]},
    {id:"temperature",name:"Temperature",icon:"◇",kinds:["warm_day","cold_day","warm_night","cold_night","cold","cold_stress","heat","heat_stress"]},
    {id:"fog",name:"Visibility",icon:"≋",kinds:["fog"]},
    {id:"flood",name:"Flooding",icon:"◈",kinds:["flood","flash_flood","river_flood"]}
  ];
  function score(p){
    return !p||!numeric(p.support_pct)?"Unknown":(p.unknown_models?"≥":"")+p.support_pct+"%";
  }
  function nextDay(d){
    const t=new Date(d+"T12:00:00Z");t.setUTCDate(t.getUTCDate()+1);return t.toISOString().slice(0,10);
  }
  function modeWindows(records){
    // Current daily assessments are comparable only when their capture matches.
    const map=new Map(),fallback=[];
    records.forEach(m=>{
      const days=m.daily_assessments||[];
      if(!days.length){
        fallback.push({start_date:m.start_date,end_date:m.end_date,label:modeScore(m),
          note:m.status==="forecast_window_passed"?"Past forecast window":"Window-level support",id:m.id});
        return;
      }
      days.forEach(p=>{
        const label=score(p),note=p.support_pct===0?"Faded":p.unknown_models?"Incomplete coverage":"";
        const old=map.get(p.start_date);
        // Duplicate records for the same kind/day share the same underlying forecast.
        if(!old||Date.parse(p.captured_at)>Date.parse(old.captured_at)){
          map.set(p.start_date,{start_date:p.start_date,end_date:p.end_date,label,note,
            id:m.id,captured_at:p.captured_at,key:JSON.stringify(p.models)});
        }
      });
    });
    const merged=[];
    [...map.values()].sort((a,b)=>a.start_date.localeCompare(b.start_date)).forEach(p=>{
      const prev=merged.at(-1);
      if(prev&&prev.label===p.label&&prev.note===p.note&&prev.key===p.key&&
          prev.captured_at===p.captured_at&&prev.id===p.id&&nextDay(prev.end_date)===p.start_date){
        prev.end_date=p.end_date;
      }else merged.push({...p});
    });
    return [...merged,...fallback].sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.end_date.localeCompare(b.end_date));
  }
  function forecastTime(p,hourly){
    if(!hourly)return short(p.time);
    const [date,time]=p.time.split("T"),hour=Number(time.slice(0,2));
    return short(date)+" "+(hour%12||12)+(hour<12?"a":"p");
  }
  function linePlot(series,quantity=false){
    const points=series.points||[],hourly=series.resolution==="hourly";
    const field=quantity?"quantity":"support_pct";
    const values=points.map(p=>p[field]).filter(numeric);
    if(!points.length||!values.length&&quantity)return '<p class="watch-muted">Quantity guidance unavailable.</p>';
    if(!values.length&&!points.some(p=>p.unknown_models>0))return '<p class="watch-muted">Forecast series unavailable.</p>';
    const x0=36,x1=310,y0=12,y1=82;
    let lo=quantity?(["F","sigma"].includes(series.unit)?Math.min(...values):0):0;
    let hi=quantity?Math.max(...values):100;
    if(quantity&&hi===lo){hi=lo+(series.unit==="F"?5:1)}
    if(quantity&&series.unit==="F"){lo-=2;hi+=2}
    if(quantity&&series.unit==="sigma"){lo=Math.min(lo,-2);hi=Math.max(hi,2)}
    const x=i=>points.length===1?(x0+x1)/2:x0+(x1-x0)*i/(points.length-1);
    const y=v=>y1-(v-lo)/(hi-lo)*(y1-y0);
    const unit=quantity?(series.unit==="F"?"°F":series.unit==="in"?"″":series.unit==="sigma"?"σ":" mph"):"%";
    const fmt=v=>(quantity&&["in","sigma"].includes(series.unit)?Number(v.toFixed(2)):Math.round(v))+unit;
    let lines="",dots="",areas="",prior=null;
    points.forEach((p,i)=>{
      const xx=x(i),v=p[field];
      if(!quantity&&p.unknown_models){
        const lower=numeric(v)?v:0;
        lines+='<line class="forecast-uncertain" x1="'+xx+'" x2="'+xx+'" y1="'+y(lower)+'" y2="'+y(p.possible_support_pct)+'"><title>'+safe(forecastTime(p,hourly))+' · '+safe(p.unknown_models)+' sources unknown</title></line>';
      }
      if(!numeric(v)){prior=null;return}
      const yy=y(v);
      if(prior){
        lines+='<line class="forecast-line '+(quantity?"quantity":"support")+'" x1="'+prior[0]+'" y1="'+prior[1]+'" x2="'+xx+'" y2="'+yy+'"/>';
        areas+='<path class="forecast-area '+(quantity?"quantity":"support")+'" d="M '+prior[0]+' '+y1+' L '+prior[0]+' '+prior[1]+' L '+xx+' '+yy+' L '+xx+' '+y1+' Z"/>';
      }
      dots+='<circle class="forecast-dot '+(quantity?"quantity":"support")+'" cx="'+xx+'" cy="'+yy+'" r="'+(hourly?1.8:3)+'"><title>'+safe(forecastTime(p,hourly))+' · '+safe(quantity?fmt(v)+" · "+p.quantity_models+" complete sources":score(p))+'</title></circle>';
      prior=[xx,yy];
    });
    const tickValues=[lo,(lo+hi)/2,hi];
    const grid=tickValues.map(v=>'<line class="watch-grid" x1="'+x0+'" x2="'+x1+'" y1="'+y(v)+'" y2="'+y(v)+'"/><text x="1" y="'+(y(v)+3)+'">'+safe(fmt(v))+'</text>').join("");
    const indexes=[...new Set([0,Math.floor((points.length-1)/2),points.length-1])];
    const ticks=indexes.map((i,k)=>'<text x="'+x(i)+'" y="100" text-anchor="'+(indexes.length===1?"middle":k===0?"start":k===indexes.length-1?"end":"middle")+'">'+safe(forecastTime(points[i],hourly))+'</text>').join("");
    const now=regionalToday();
    const current=points.findIndex(p=>p.time.slice(0,10)===now);
    const currentMarker=!hourly&&current>=0?'<line class="forecast-now" x1="'+x(current)+'" x2="'+x(current)+'" y1="'+y0+'" y2="'+y1+'"><title>Today · Central time</title></line>':"";
    return '<svg class="forecast-plot" viewBox="0 0 320 106" role="img" aria-label="'+(quantity?"Forecast quantity":"Model support")+' by forecast valid time">'+grid+areas+currentMarker+lines+dots+ticks+'</svg>';
  }
  function forecastPanel(series){
    if(!series?.points?.length)return '<p class="watch-muted">No forecast-time chart available.</p>';
    const hourly=series.resolution==="hourly",unit=series.unit==="F"?"°F":series.unit==="sigma"?"σ":series.unit;
    const threshold=(series.direction==="lte"?"≤":"≥")+" "+series.threshold+" "+unit;
    const quantity=series.unit!=="signal";
    return '<div class="forecast-series"><div class="forecast-series-head"><b>'+(hourly?"Hourly":"Daily")+' outlook</b><small>Central time · forecast, not observation</small></div>'+
      (quantity?'<div class="forecast-plot-label"><span class="forecast-key quantity"></span> '+safe(series.unit==="in"?(hourly?"Amount per hour":"Amount per day"):series.unit==="mph"?"Regional peak gust":series.unit==="sigma"?"Departure from historical normal":"Regional temperature extreme")+' · '+safe(unit)+'</div>'+linePlot(series,true)+'<small class="watch-muted">Median of complete models’ regional extrema, not a local total.</small>':"")+
      '<div class="forecast-plot-label"><span class="forecast-key support"></span> Model support (%)</div>'+linePlot(series,false)+
      '<small class="watch-muted">'+safe(series.unit==="signal"?"Matching weather code":threshold)+' per '+(hourly?"hour":"day")+' · dashed bars = missing coverage, not a calibrated chance.</small></div>';
  }
  const artwork={
    snow:'<path d="M24 5v38M7.5 14.5l33 19M7.5 33.5l33-19M19 8l5 5 5-5M19 40l5-5 5 5M8 20l7-2-2-7M40 28l-7 2 2 7M13 37l2-7-7-2M35 11l-2 7 7 2"/>',
    rain:'<path d="M13 28a9 9 0 0 1 1-18 12 12 0 0 1 22 5 7 7 0 0 1 1 13H13M15 33l-3 6m13-6-3 6m13-6-3 6"/>',
    severe:'<path d="M9 27a8 8 0 0 1 5-15 11 11 0 0 1 21 3 7 7 0 0 1 3 13h-8M25 21l-9 13h9l-3 10 12-16h-9z"/>',
    wind:'<path d="M5 15h28a5 5 0 1 0-5-5M5 24h35a4 4 0 1 1-4 4M5 33h19a5 5 0 1 1-5 5"/>',
    temperature:'<path d="M20 29V10a4 4 0 0 1 8 0v19a9 9 0 1 1-8 0zM24 17v17M33 14h6m-6 8h4"/><circle cx="24" cy="36" r="3"/>',
    fog:'<path d="M8 15a8 8 0 0 1 8-6 10 10 0 0 1 19 5 6 6 0 0 1 6 6M5 24h38M10 31h28M5 38h38"/>',
    flood:'<path d="M5 13q5-6 10 0t10 0t10 0t10 0M5 24q5-6 10 0t10 0t10 0t10 0M5 35q5-6 10 0t10 0t10 0t10 0"/>',
    other:'<circle cx="24" cy="24" r="15"/><path d="M24 3v8m0 26v8M3 24h8m26 0h8"/>'
  };
  function weatherArt(id,cls="wx-icon"){
    return '<svg class="'+safe(cls)+'" viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+(artwork[id]||artwork.other)+'</svg>';
  }
  function activeFamilies(modes){
    const known=new Set(families.flatMap(f=>f.kinds));
    const other=[...new Set(modes.filter(m=>!known.has(m.kind)).map(m=>m.kind))];
    return [...families,{id:"other",name:"Other weather",icon:"◎",kinds:other}].filter(f=>modes.some(m=>f.kinds.includes(m.kind)));
  }
  function seriesFor(group,kind,records){
    const forecasts=group.forecast_modes?.[kind];
    if(forecasts?.hourly||forecasts?.daily)return forecasts;
    const raw=records.flatMap(m=>m.daily_assessments||[]).map(p=>({...p,time:p.start_date}));
    const points=[...new Map(raw.map(p=>[p.time,p])).values()].sort((a,b)=>a.time.localeCompare(b.time));
    return {daily:points.length?{resolution:"daily",unit:"signal",points,legacy:true}:null,hourly:null};
  }
  function peakSummary(series){
    const points=series?.points||[];
    const positive=points.filter(p=>numeric(p.support_pct)&&p.support_pct>0);
    const supported=positive.reduce((a,p)=>!a||p.support_pct>a.support_pct?p:a,null);
    const quantified=points.filter(p=>numeric(p.quantity));
    const peak=quantified.reduce((a,p)=>!a||(series.direction==="lte"?p.quantity<a.quantity:p.quantity>a.quantity)?p:a,null);
    const allClear=points.length>0&&points.every(p=>p.support_pct===0&&!p.unknown_models);
    const hourly=series?.resolution==="hourly";
    const unit=series?.unit==="F"?"°F":series?.unit==="in"?(hourly?"″/hr":"″/day"):series?.unit==="sigma"?"σ":" mph";
    const amount=peak?Number(peak.quantity.toFixed(["in","sigma"].includes(series.unit)?2:0))+unit:null;
    return {support:supported?score(supported):allClear?"0%":"Unknown",
      time:supported?forecastTime(supported,hourly):null,amount,
      amountTime:peak?forecastTime(peak,hourly):null,allClear};
  }
  function eventRibbon(group,modes,fs){
    const start=group.start_date,end=group.end_date;
    const days=[];
    for(let day=start;day<=end&&days.length<32;day=nextDay(day))days.push(day);
    if(!days.length)return "";
    const today=regionalToday();
    const rows=fs.map(f=>{
      const records=modes.filter(m=>f.kinds.includes(m.kind));
      const points=records.flatMap(m=>seriesFor(group,m.kind,records.filter(r=>r.kind===m.kind)).daily?.points||[]);
      return '<div class="wx-ribbon-row wx-'+f.id+'"><span class="wx-ribbon-icon" title="'+safe(f.name)+'">'+weatherArt(f.id)+'</span>'+
        days.map(day=>{
          const ps=points.filter(p=>p.time===day);
          const hit=ps.some(p=>numeric(p.support_pct)&&p.support_pct>0);
          const unknown=!ps.length||ps.some(p=>!numeric(p.support_pct)||p.unknown_models);
          const status=hit?"supported":unknown?"unknown":"clear";
          return '<span class="wx-ribbon-cell '+status+(day===today?" today":"")+'" title="'+safe(short(day)+" · "+f.name+" · "+(hit?"At least one model supports a mode":unknown?"Incomplete or missing data":"No threshold support"))+'"></span>';
        }).join("")+'</div>';
    }).join("");
    return '<section class="wx-ribbon"><div class="wx-ribbon-head"><b>Event at a glance</b><span>Regional forecast timeline</span></div>'+
      '<div class="wx-ribbon-scroll"><div class="wx-ribbon-grid" style="--days:'+days.length+'"><div class="wx-ribbon-row wx-ribbon-dates"><span></span>'+days.map(day=>'<span class="'+(day===today?"today":"")+'">'+safe(short(day))+'</span>').join("")+'</div>'+rows+'</div></div>'+
      '<div class="wx-ribbon-legend"><span>● Model signal</span><span>▧ Incomplete coverage</span><span>– No support</span></div></section>';
  }
  function familyRows(modes,group,featured){
    return activeFamilies(modes).map(f=>{
      const members=modes.filter(m=>f.kinds.includes(m.kind));
      const kindsIn=[...new Set(members.map(m=>m.kind))];
      const primaryKind=members.find(m=>m.id===group.primary_mode_id)?.kind;
      const rows=kindsIn.map((kind,i)=>{
        const records=members.filter(m=>m.kind===kind).sort((a,b)=>a.start_date.localeCompare(b.start_date));
        const forecasts=seriesFor(group,kind,records),main=forecasts.hourly||forecasts.daily;
        const p=peakSummary(main);
        const open=featured&&!["snow","rain","flood"].includes(f.id)&&(primaryKind?kind===primaryKind:i===0);
        return '<details class="wx-mode" '+(open?'open':'')+'><summary class="wx-mode-summary"><span><b>'+safe(modeLabel(records[0]))+'</b><small>'+safe(main?(p.time?"Peak support · "+p.time:p.allClear?"No threshold support":"Incomplete guidance"):"Awaiting guidance")+'</small></span><span class="wx-mode-number"><strong>'+safe(p.support)+'</strong><small>model support</small></span><span class="wx-chevron" aria-hidden="true">⌄</span></summary>'+
          '<div class="wx-mode-body">'+(p.amount?'<div class="wx-peak"><span>Modeled '+(main.direction==="lte"?"minimum":"peak")+'</span><strong>'+safe(p.amount)+'</strong><small>'+safe(p.amountTime)+'</small></div>':"")+
          (main?(main.legacy?'<p class="watch-muted">Legacy daily support only.</p>'+linePlot(main):forecastPanel(main)):'<p class="watch-muted">Awaiting forecast-time series.</p>')+
          (forecasts.hourly&&forecasts.daily?'<details class="event-mode-history"><summary>Full event daily outlook</summary>'+forecastPanel(forecasts.daily)+'</details>':"")+
          '<details class="event-mode-history"><summary>Forecast evolution · previous model updates</summary>'+
            records.map(m=>'<p class="watch-muted">'+safe(dateRange(m))+' · '+safe(describe(m.status))+'</p>'+watchChart(m.assessments)+'<button type="button" class="event-case-button" data-event-id="'+safe(m.id)+'">Mode details →</button>').join("")+
          '</details></div></details>';
      }).join("");
      return '<section class="wx-family wx-'+f.id+'"><div class="wx-family-head">'+weatherArt(f.id,"wx-watermark")+'<div class="wx-icon-box">'+weatherArt(f.id)+'</div><div><h4>'+safe(f.name)+'</h4><small>'+kindsIn.length+' weather mode'+(kindsIn.length===1?"":"s")+'</small></div></div><div class="wx-family-modes">'+rows+'</div></section>';
    }).join("");
  }
  // Glance layer: one chip per weather family, labelled by peak model agreement.
  const chipNames={snow:"Snow",light_snow:"Flurries",ice:"Ice",rain:"Rain",severe:"Storms",wind:"Wind",fog:"Fog",flood:"Flooding",
    warm_day:"Warm day",cold_day:"Cold day",warm_night:"Warm night",cold_night:"Cold night",cold:"Cold",cold_stress:"Cold",heat:"Heat",heat_stress:"Heat"};
  // Share of models supporting a mode in a capture (not a calibrated probability), and its 24h trend.
  const supportOf=a=>a&&numeric(a.support_pct)?a.support_pct:null;
  const when=s=>new Intl.DateTimeFormat("en-US",{timeZone:CT,weekday:"short",hour:"numeric"}).format(new Date(s));
  function supportTrend(m){
    const hist=(m.assessments||[]).filter(a=>supportOf(a)!==null&&a.captured_at);
    const last=hist.at(-1);
    if(!last)return null;
    // Faded: not in the latest capture, so current support is zero; compare with its last value.
    if(m.status!=="in_latest_capture")return {pct:0,prior:last.support_pct,priorAt:last.captured_at,models:0,total:last.total_models,unknown:0,faded:true};
    const t=Date.parse(last.captured_at);
    const older=hist.filter(a=>Date.parse(a.captured_at)<=t-6*3600000);
    const prior=older.reduce((b,a)=>!b||Math.abs(Date.parse(a.captured_at)-(t-864e5))<Math.abs(Date.parse(b.captured_at)-(t-864e5))?a:b,null);
    const upper=numeric(last.possible_support_pct)&&last.possible_support_pct>last.support_pct?last.possible_support_pct:null;
    return {pct:last.support_pct,upper,prior:prior?prior.support_pct:null,priorAt:prior?.captured_at||null,
      models:last.supporting_models,total:last.total_models,unknown:last.unknown_models||0};
  }
  function trendMark(t){
    if(t.prior===null)return {cls:"new",sym:"new",text:"No earlier forecast to compare yet"};
    const d=t.pct-t.prior,since=" ("+t.prior+"% on "+when(t.priorAt)+")";
    if(t.faded)return {cls:"down",sym:"↓",text:"Dropped out of the latest forecasts"+since};
    if(d>=25)return {cls:"up",sym:"↑",text:"Rising"+since};
    if(d<=-25)return {cls:"down",sym:"↓",text:"Falling"+since};
    return {cls:"flat",sym:"→",text:"Steady"+since};
  }
  // Highest hourly model share over the rest of the event for one family (null if no hourly coverage ahead).
  function remainingShare(group,f,modes){
    const now=nowHourKey();let best=null;
    [...new Set(modes.filter(m=>f.kinds.includes(m.kind)).map(m=>m.kind))].forEach(kind=>{
      (group.forecast_modes?.[kind]?.hourly?.points||[]).forEach(p=>{
        if(p.time>=now&&numeric(p.support_pct))best=Math.max(best??0,p.support_pct);
      });
    });
    return best;
  }
  function glanceChips(group,modes,isCurrent=false){
    return activeFamilies(modes).map(f=>{
      let best=null;
      modes.filter(m=>f.kinds.includes(m.kind)).forEach(m=>{
        const t=supportTrend(m);
        if(t&&(!best||t.pct>best.t.pct||(t.pct===best.t.pct&&m.kind==="snow")))best={kind:m.kind,m,t};
      });
      if(!best||(best.t.pct<=0&&!(best.t.prior>0)))return null;
      const name=["snow","temperature"].includes(f.id)?(chipNames[best.kind]||modeLabel(best.m)):(chipNames[f.id]||f.name);
      const rest=isCurrent?remainingShare(group,f,modes):null;
      return {f,name,pct:rest??best.t.pct,t:best.t,trend:trendMark(best.t),rest};
    }).filter(Boolean).sort((a,b)=>b.pct-a.pct);
  }
  const weekday=d=>new Intl.DateTimeFormat("en-US",{timeZone:CT,weekday:"short"}).format(new Date(d+"T12:00:00Z"));
  const longDate=d=>weekday(d)+" "+short(d);
  function whenLabel(group){
    const today=regionalToday();
    if(group.start_date<=today)return group.end_date===today?"Now · ends today":"Now · through "+weekday(group.end_date);
    const days=Math.round((Date.parse(group.start_date+"T12:00:00Z")-Date.parse(today+"T12:00:00Z"))/86400000);
    return days===1?"Tomorrow":"In "+days+" days";
  }
  // Hour-by-hour strip for an event in progress: share of models per hour, per weather family (Central time).
  function nowHourKey(){
    const v=Object.fromEntries(new Intl.DateTimeFormat("en-US",{timeZone:CT,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",hourCycle:"h23"}).formatToParts(new Date()).map(p=>[p.type,p.value]));
    return v.year+"-"+v.month+"-"+v.day+"T"+v.hour+":00";
  }
  const hourLabel=t=>{const h=Number(t.slice(11,13));return (h%12||12)+(h<12?"a":"p")};
  function hourlyStrip(group,modes,limit=24){
    const now=nowHourKey(),rows=[];
    activeFamilies(modes).forEach(f=>{
      const byTime=new Map();let unit=null;
      [...new Set(modes.filter(m=>f.kinds.includes(m.kind)).map(m=>m.kind))].forEach(kind=>{
        const h=group.forecast_modes?.[kind]?.hourly;if(!h)return;
        if(h.unit==="in")unit="in";
        h.points.forEach(p=>{
          if(p.time<now)return;
          const c=byTime.get(p.time)||{pct:null,unknown:false,qty:null};
          if(numeric(p.support_pct))c.pct=Math.max(c.pct??0,p.support_pct);
          if(!numeric(p.support_pct)||p.unknown_models)c.unknown=true;
          if(h.unit==="in"&&numeric(p.quantity))c.qty=Math.max(c.qty??0,p.quantity);
          byTime.set(p.time,c);
        });
      });
      if(byTime.size)rows.push({f,byTime,unit});
    });
    const hours=[...new Set(rows.flatMap(r=>[...r.byTime.keys()]))].sort().slice(0,limit);
    if(!rows.length||!hours.length)return "";
    const day=t=>weekday(t.slice(0,10));
    const head='<div class="wx-hr-row wx-hr-head"><span></span>'+hours.map((t,i)=>{
      const h=Number(t.slice(11,13)),show=i===0||h%3===0;
      return '<span>'+(i===0?"Now":show?(h===0?day(t):hourLabel(t)):"")+'</span>';
    }).join("")+'</div>';
    const body=rows.map(r=>{
      const name=chipNames[r.f.id]||r.f.name;
      return '<div class="wx-hr-row wx-'+r.f.id+'"><span class="wx-hr-name" title="'+safe(r.f.name)+'">'+weatherArt(r.f.id)+'</span>'+hours.map(t=>{
        const c=r.byTime.get(t);
        if(!c)return '<span class="wx-hr-cell none" title="'+safe(day(t)+" "+hourLabel(t)+" · "+name+": no hourly forecast")+'"></span>';
        const lvl=c.pct===null?"unk":"l"+Math.min(4,Math.round(c.pct/25));
        const amt=r.unit==="in"&&numeric(c.qty)&&c.qty>0?" · up to "+c.qty.toFixed(2)+" in/hr (regional)":"";
        return '<span class="wx-hr-cell '+lvl+(c.unknown&&c.pct!==null?" partial":"")+'" title="'+safe(day(t)+" "+hourLabel(t)+" · "+name+": "+(c.pct===null?"no model data":c.pct+"% of models")+amt)+'"></span>';
      }).join("")+'</div>';
    }).join("");
    const quiet=rows.every(r=>hours.every(t=>{const c=r.byTime.get(t);return !c||(c.pct===0&&!c.unknown)}));
    const last=hours.at(-1);
    return '<div class="wx-hourly"><div class="wx-hourly-title"><b>Hour by hour</b><span>Share of models showing it each hour · Central time</span></div>'+
      (quiet?'<p class="wx-hr-quiet">No model shows more of this weather from now through '+safe(day(last)+" "+hourLabel(last))+'.</p>':"")+
      '<div class="wx-hr-scroll"><div class="wx-hr-grid" style="--hours:'+hours.length+'">'+head+body+'</div></div>'+
      '<div class="wx-hr-legend"><span class="wx-hr-cell l1"></span>25%<span class="wx-hr-cell l2"></span>50%<span class="wx-hr-cell l3"></span>75%<span class="wx-hr-cell l4"></span>100%</div></div>';
  }
  function groupCard(group,isCurrent=false){
    const modes=modeItems(group),fs=activeFamilies(modes),primary=modes.find(m=>m.id===group.primary_mode_id)||modes[0];
    const chips=glanceChips(group,modes,isCurrent);
    const pf=chips.find(c=>c.pct>0)?.f||fs.find(f=>f.kinds.includes(primary?.kind))||{id:"other"};
    const tone=marker(group.status),faded=group.status!=="in_latest_capture";
    const shown=chips.slice(0,4),extra=chips.length-shown.length;
    const header='<div class="wx-event-hero wx-glance wx-'+pf.id+'">'+
      '<div class="wx-glance-top"><div class="wx-icon-box">'+weatherArt(pf.id)+'</div>'+
      '<div class="wx-glance-name"><h3>'+safe(group.name)+'</h3><p>'+safe(longDate(group.start_date)+(group.start_date===group.end_date?"":" – "+longDate(group.end_date))+(isCurrent&&routine(group)?" · Routine weather":""))+'</p></div>'+
      '<span class="wx-when'+(isCurrent?" now":"")+'">'+safe(whenLabel(group))+'</span></div>'+
      (shown.length?'<div class="wx-chips">'+shown.map(c=>{
        const of=numeric(c.t.models)&&numeric(c.t.total)?c.t.models+" of "+c.t.total+" models":c.pct+"% of models";
        const tip=c.name+": "+(c.t.faded?"no models show it now":of+" show it")+(c.t.unknown?"; "+c.t.unknown+" had no data for these dates, so the share could be up to "+(c.t.upper??c.pct)+"%":"")+". "+c.trend.text+". Share of models, not a calibrated probability.";
        if(c.rest!==null&&c.rest!==undefined){
          const restTip=c.name+": "+(c.rest===0?"no model shows more after now (earlier in this event up to "+c.t.pct+"%)":"up to "+c.rest+"% of models in the remaining hours")+". Share of models, not a calibrated probability.";
          return '<span class="wx-chip wx-'+c.f.id+(c.rest<50?" low":"")+'" title="'+safe(restTip)+'">'+weatherArt(c.f.id)+safe(c.name)+' <b>'+(c.rest===0?"done":safe(c.rest)+'%')+'</b>'+(c.rest===0?"":'<i class="wx-trend rest">rest of event</i>')+'</span>';
        }
        return '<span class="wx-chip wx-'+c.f.id+(c.pct<50?" low":"")+'" title="'+safe(tip)+'">'+weatherArt(c.f.id)+safe(c.name)+' <b>'+safe(c.t.upper?c.pct+"–"+c.t.upper:c.pct)+'%</b><i class="wx-trend '+c.trend.cls+'" aria-label="'+safe(c.trend.text)+'">'+safe(c.trend.sym)+'</i></span>';
      }).join("")+(extra>0?'<span class="wx-chip more">+'+extra+'</span>':"")+'</div>':
        '<p class="wx-glance-note">'+(faded?"Faded: no model shows it in the latest guidance.":"No model currently shows this weather.")+'</p>')+
      (isCurrent?hourlyStrip(group,modes.filter(m=>m.status==="in_latest_capture")):"")+
      (faded&&shown.length?'<p class="wx-glance-note">'+safe(isCurrent?"Signal has faded in the latest guidance.":"Faded from the latest model guidance.")+'</p>':"")+
      '</div>';
    const contents=eventRibbon(group,modes,fs)+'<div class="wx-families">'+familyRows(modes,group,false)+'</div><p class="watch-muted wx-source">Regional forecast guidance. Model support is not a calibrated probability. Hourly and daily thresholds differ.</p>'+
      '<div class="wx-event-footer"><small>First seen '+safe(short(group.first_seen))+'</small><button type="button" class="event-case-button" data-event-id="'+safe(primary?.id||group.primary_mode_id)+'">Storm files →</button></div>';
    return '<article class="event-signal wx-event '+(isCurrent?"wx-featured":"wx-upcoming")+(isCurrent&&routine(group)?" wx-routine":"")+' '+safe(tone)+'">'+header+
      '<details class="wx-upcoming-detail"><summary>Forecast details <span aria-hidden="true">⌄</span></summary>'+contents+'</details></article>';
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
  function regionalToday(){
    const parts=new Intl.DateTimeFormat("en-US",{timeZone:CT,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());
    const values=Object.fromEntries(parts.map(p=>[p.type,p.value]));
    return values.year+"-"+values.month+"-"+values.day;
  }
  // Lifecycle: an event is in progress only while the latest guidance still shows some of its weather ahead.
  // Faded modes (not in the latest capture) no longer stretch the window.
  function liveWindow(group){
    const active=modeItems(group).filter(m=>m.status==="in_latest_capture");
    if(!active.length)return null;
    return {start:active.reduce((a,m)=>m.start_date<a?m.start_date:a,active[0].start_date),
      end:active.reduce((a,m)=>m.end_date>a?m.end_date:a,active[0].end_date),modes:active};
  }
  function weatherAhead(group,modes){
    const now=nowHourKey(),today=regionalToday();
    const open=p=>{const v=numeric(p.possible_support_pct)?p.possible_support_pct:p.support_pct;return !numeric(v)||v>0};
    return [...new Set(modes.map(m=>m.kind))].some(kind=>{
      const fm=group.forecast_modes?.[kind];
      if(!fm)return true; // no series to check: do not declare it over
      let covered="";
      for(const p of fm.hourly?.points||[]){
        if(p.time>covered)covered=p.time;
        if(p.time>=now&&open(p))return true;
      }
      // Days the hourly series does not reach through 11 PM fall back to the daily share.
      return (fm.daily?.points||[]).some(p=>p.time>=today&&covered<p.time+"T23:00"&&open(p));
    });
  }
  function calmState(next){
    if(!next)return '<div class="wx-calm"><b>No weather event right now.</b><span>No upcoming events are being tracked in the current forecast range.</span></div>';
    const c=glanceChips(next,modeItems(next))[0];
    const share=c?" · "+c.name+" "+(c.t.upper?c.pct+"–"+c.t.upper:c.pct)+"%":"";
    return '<div class="wx-calm"><b>No weather event right now.</b><span>Next up: <strong>'+safe(next.name)+'</strong> · '+
      safe(longDate(next.start_date))+' · '+safe(whenLabel(next).toLowerCase())+safe(share)+'</span></div>';
  }
  function renderHorizon(){
    const index=state.index;
    if(!index){
      set("currentEventStatus","CATALOG UNAVAILABLE");
      set("eventWatchStatus","CATALOG UNAVAILABLE");
      q("currentEventList").innerHTML='<div class="blank-state">Current named events cannot be verified until the archived catalog is available.</div>';
      q("eventWatchList").innerHTML='<div class="blank-state">Upcoming event tracking is waiting for a valid archived event feed.</div>';
      return;
    }
    const fresh=freshness(index),today=regionalToday();
    const display=groups(index).filter(x=>x.end_date>=today&&x.status!=="forecast_window_passed")
      .sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.id.localeCompare(b.id));
    const now=[],upcoming=[];
    display.forEach(g=>{
      if(g.start_date>today){upcoming.push(g);return}
      const lw=liveWindow(g);
      if(!lw||lw.end<today)return; // only faded weather left: over
      const trimmed={...g,start_date:lw.start,end_date:lw.end};
      if(lw.start>today)upcoming.push(trimmed);
      else if(weatherAhead(g,lw.modes))now.push(trimmed);
      // else: no model shows any more of it, so it has ended
    });
    upcoming.sort((a,b)=>a.start_date.localeCompare(b.start_date)||a.id.localeCompare(b.id));
    set("currentEventStatus",fresh?(now.length?now.length+" in progress":"No event now"):"CATALOG UPDATES DELAYED");
    set("eventWatchStatus",fresh?upcoming.length+" upcoming event(s)":"CATALOG UPDATES DELAYED");
    q("currentEventList").innerHTML=now.length?
      now.map(g=>groupCard(g,true)).join(""):
      (fresh?calmState(upcoming[0]):'<div class="blank-state">No current event is listed in the last available catalog; updates are delayed.</div>');
    set("currentEventSource","Local calendar: Central Time · "+stamp(index.source_latest_capture)+
      (fresh?"":" · STALE")+" · Current forecast window does not confirm that all forecast weather is occurring.");
    if(upcoming.length){
      const notable=upcoming.filter(x=>!routine(x)), everyday=upcoming.filter(routine);
      q("eventWatchList").innerHTML=(notable.length?notable.map(g=>groupCard(g)).join(""):"")+
        (everyday.length?'<details class="routine-watches" open><summary>Routine weather events · '+everyday.length+'</summary><div class="event-watch-list">'+everyday.map(g=>groupCard(g)).join("")+'</div></details>':"");
    }else{
      q("eventWatchList").innerHTML='<div class="blank-state">No upcoming event windows are listed in the published regional catalog.</div>';
    }
    set("eventWatchSource","Temperature climatology: "+(index.climatology?.status==="available"?Object.keys(index.climatology.places||{}).length+"/8 locations · NOAA 1991–2020":"not available")+" · Source snapshot: "+stamp(index.source_latest_capture)+
      (fresh?"":" · STALE — waiting for scheduled collector")+
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
    const unit=summary.threshold?.unit==="sigma"?"σ":summary.threshold?.unit||"";
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
    const openEvent=ev=>{
      const button=ev.target.closest("[data-event-id]");if(!button)return;
      state.selected=button.dataset.eventId;
      document.querySelector('[data-tab="archive"]')?.click();
      renderArchiveList();
      q("trackedCasesTitle")?.scrollIntoView({behavior:"smooth",block:"start"});
    };
    q("eventWatchList")?.addEventListener("click",openEvent);
    q("currentEventList")?.addEventListener("click",openEvent);
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
