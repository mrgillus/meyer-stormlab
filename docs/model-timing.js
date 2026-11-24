/* Provider model metadata, not the provenance of every stitched forecast hour. */
(()=>{const host=document.getElementById('modelTiming');if(!host)return;
const feeds=[['GFS · 0.13°','ncep_gfs013'],['GFS · 0.25°','ncep_gfs025'],['ECMWF · IFS 0.25°','ecmwf_ifs025'],['GEM · GDPS','cmc_gem_gdps_15km'],['ICON · Global','dwd_icon']];
const fmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/Chicago',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
const stamp=s=>Number.isFinite(s)&&s>0?fmt.format(new Date(s*1000)):'—';
const z=s=>Number.isFinite(s)&&s>0?String(new Date(s*1000).getUTCHours()).padStart(2,'0')+'Z':'—';
const dur=s=>{let m=Math.max(0,Math.ceil(Math.abs(s)/60));return Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m'};
const table=host.querySelector('table'),status=host.querySelector('[data-status]'),btn=host.querySelector('button');
let records=new Map(),busy=false,lastChecked=null;

const comparisonFeeds={ncep_gfs_global:[['0.13°','ncep_gfs013'],['0.25°','ncep_gfs025']],ecmwf_ifs025:[['','ecmwf_ifs025']],cmc_gem_gdps:[['','cmc_gem_gdps_15km']],icon_global:[['','dwd_icon']]};
const clock=new Intl.DateTimeFormat('en-US',{timeZone:'America/Chicago',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
const day=new Intl.DateTimeFormat('en-US',{timeZone:'America/Chicago',year:'numeric',month:'numeric',day:'numeric'});
const compactStamp=(s,now)=>day.format(new Date(s*1000))===day.format(new Date(now*1000))?clock.format(new Date(s*1000)):stamp(s);
function renderComparison(){
 const now=Date.now()/1000;
 for(const card of document.querySelectorAll('#cards [data-model-id]')){
  const box=card.querySelector('[data-model-eta]');if(!box)continue;
  const list=comparisonFeeds[card.dataset.modelId]||[];
  const frag=document.createDocumentFragment();
  for(const [label,id] of list){
   const r=records.get(id)||{},d=r.data;
   const line=document.createElement('div');line.className='model-eta-line';
   const value=document.createElement('span');value.className='model-eta-value';
   const detail=document.createElement('span');detail.className='model-eta-detail';
   const prefix=label?label+' · ':'';
   if(d){
    const eta=d.last_run_availability_time+d.update_interval_seconds+600;
    const stale=!!r.error||now-r.checked>900;
    value.textContent=prefix+'~'+compactStamp(eta,now);
    detail.textContent=stale?'Last known · status unavailable':now>=eta?'Awaiting update':dur(eta-now)+' remaining';
    line.classList.toggle('overdue',!stale&&now>=eta);
    line.classList.toggle('stale',stale);
    line.title='Estimated provider availability, not a guaranteed forecast refresh. Last published run: '+z(d.last_run_initialisation_time)+' · '+stamp(d.last_run_availability_time);
   }else{value.textContent=prefix+(r.error?'Unavailable':'Checking…');detail.textContent=r.error?'No verified refresh time':'';line.classList.add('stale');}
   line.append(value,detail);frag.append(line);
  }
  box.replaceChildren(frag);
 }
}

function render(){const now=Date.now()/1000;table.replaceChildren();const head=table.createTHead().insertRow();['Model','Latest run (CT)','Available (CT)','Next run (CT)','Next data ETA (CT)','Status'].forEach(x=>{let th=document.createElement('th');th.textContent=x;head.append(th)});
const body=table.createTBody();for(const [name,id] of feeds){const r=records.get(id)||{},d=r.data;const row=body.insertRow();let values=[name,'—','—','—','—',r.error||'Checking…'];if(d){let i=d.last_run_initialisation_time,a=d.last_run_availability_time,n=d.update_interval_seconds;let next=i+n,eta=a+n+600;let st=now<eta?'~'+dur(eta-now)+' remaining':'Awaiting update · '+dur(now-eta)+' past ETA';if(r.error)st='Last known · '+r.error;else if(now-r.checked>900)st='Status check stale · '+st;values=[name,z(i)+' · '+stamp(i),stamp(a),z(next)+' · '+stamp(next),'~'+stamp(eta),st];}
values.forEach((v,j)=>{let c=row.insertCell();c.textContent=v;if(j===0)c.style.fontWeight='700';if(j===5&&d&&!r.error&&now>d.last_run_availability_time+d.update_interval_seconds+600)c.style.color='#f4b65c'});}
status.textContent=lastChecked?'Metadata checked '+fmt.format(lastChecked)+' · refreshes every 5 minutes while open.':'Fetching official model metadata…';renderComparison();}
async function refresh(){if(busy)return;busy=true;btn.disabled=true;try{await Promise.all(feeds.map(async ([name,id])=>{try{let res=await fetch('https://api.open-meteo.com/data/'+id+'/static/meta.json?t='+Date.now(),{cache:'no-store',signal:AbortSignal.timeout(20000)});if(!res.ok)throw Error('HTTP '+res.status);let d=await res.json();let i=d.last_run_initialisation_time,a=d.last_run_availability_time,n=d.update_interval_seconds;let now=Date.now()/1000;if(![i,a,n].every(Number.isFinite)||i<=0||a<i||a>now+300||n<600||n>86400*2)throw Error('Invalid metadata');records.set(id,{data:d,checked:now});}catch(e){records.set(id,{...records.get(id),error:e.message||'Unavailable'});}}));lastChecked=new Date();}finally{busy=false;btn.disabled=false;render()}}
btn.addEventListener('click',refresh);const cards=document.getElementById('cards');if(cards)new MutationObserver(renderComparison).observe(cards,{childList:true});document.getElementById('refresh')?.addEventListener('click',refresh);render();refresh();setInterval(refresh,300000);setInterval(render,60000);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
})();