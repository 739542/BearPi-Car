'use strict';
const el=id=>document.getElementById(id), key=location.hash.slice(1), now=()=>performance.now();
const note=t=>el('note').textContent=t;
const clamp=v=>Math.max(-1,Math.min(1,v));
let x=0,y=0,session='',epoch=0,seq=0,arming=false,sending=false,stops=0,online=false;
let held={gas:null,steer:null},locked=false,rec=null,replay=null,choice='snake',race=null;
let sport=false;
const choices={
 orbit:{name:'定点环绕',frames:[[0,.23,.82],[8000,0,0]]},
 parade:{name:'组合秀',frames:[[0,0,.75],[750,.44,.82],[1900,-.44,.82],[3050,0,0],[3350,-.33,-.78],[4600,.33,-.78],[5850,0,0]]},
 snake:{name:'蛇形绕桩',frames:[[0,.45,.85],[900,-.45,.85],[1800,.45,.85],[2700,-.45,.85],[3600,0,0]]},
 eight:{name:'8 字巡游',frames:[[0,.55,.9],[3200,-.55,.9],[6400,0,0]]}
};
function load(k){try{return JSON.parse(localStorage.getItem(k))}catch{return null}}
function save(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch{note('浏览器未能保存记录')}}
const stored=load('bearpi-record');
if(stored?.frames?.length>=2)choices.saved=stored;
function stamp(ms){return new Date(ms).toISOString().slice(14,21)}
async function api(action,body={}){
 const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),1000);
 try{const r=await fetch('/control',{method:'POST',headers:{'Content-Type':'application/json','X-Car-Key':key},body:JSON.stringify({action,...body}),signal:ctrl.signal});
 if(!r.ok)throw Error(r.status===403?'请用完整配对链接打开':(await r.json()).error);
 return await r.json()}finally{clearTimeout(timer)}
}
function state(ok){online=ok;el('status').classList.toggle('online',ok);el('status').textContent=ok?'● 已连接':'○ 未连接'}
function frame(){if(rec){const last=rec.frames.at(-1);if(!last||x!==last[1]||y!==last[2])rec.frames.push([Math.round(now()-rec.start),x,y])}}
function resetAxes(){x=y=0;frame();paint()}
function finishRecord(){
 if(!rec)return;
 rec.frames.push([Math.round(now()-rec.start),0,0]);
 if(rec.frames.some(f=>Math.abs(f[2])>.07)){choices.saved={name:'我的绕桩',fast:sport,frames:rec.frames};save('bearpi-record',choices.saved);choice='saved';note('已保存本次驾驶')}
 rec=null;el('record').textContent='开始录制';el('record').classList.remove('on');select(choice)
}
async function halt(reason,emergency=false){
 epoch++;session='';arming=false;replay=null;
 if(emergency)locked=true;
 resetAxes();note(reason);el('play').textContent='开始重演';
 stops++;
 try{await api('stop');state(true)}catch(e){state(false);note(e.message)}finally{stops--;if(held.gas===null&&held.steer===null)locked=false}
}
async function arm(fast=sport){
 if(stops||locked||!online||arming||session)return false;
 const generation=++epoch;arming=true;
 try{const r=await api('arm',{fast});if(generation!==epoch)return false;
 session=r.session;seq=0;return true}
 catch(e){if(generation===epoch){locked=true;resetAxes();note(e.message)}return false}
 finally{if(generation===epoch)arming=false}
}
async function pump(){
 if(!session||sending)return;
 const generation=epoch;sending=true;
 try{await api('drive',{session,seq:++seq,x,y,reverse:!!replay?.reverse})}
 catch(e){if(generation===epoch)await halt(e.message,true)}
 finally{sending=false}
}
function pos(id,e){
 const r=el(id).querySelector('.track').getBoundingClientRect();
 if(id==='gas')y=clamp(1-2*(e.clientY-r.top)/r.height);
 else x=clamp(2*(e.clientX-r.left)/r.width-1);
 frame();paint()
}
for(const id of ['gas','steer']){
 const pad=el(id);
 pad.onpointerdown=async e=>{
 if(locked||stops||!online||held[id]!==null)return;
 held[id]=e.pointerId;pad.setPointerCapture(e.pointerId);pos(id,e);
 if(id==='gas'&&await arm())pump()
 };
 pad.onpointermove=e=>{if(!locked&&held[id]===e.pointerId)pos(id,e)};
 const end=e=>{
 if(held[id]!==e.pointerId)return;
 held[id]=null;
 if(id==='gas'){y=0;frame();if(session||arming)halt('油门已松开')}
 else{x=0;frame();if(session)pump()}
 if(held.gas===null&&held.steer===null)locked=false;
 paint()
 };
 pad.onpointerup=end;pad.onlostpointercapture=end;pad.onpointercancel=end;
}
function centerStop(){halt('已急停，松开双手后可重新操作',true);finishRecord()}
el('stop').onclick=centerStop;
function leave(){if(session||arming||replay)halt('已停车',true);held={gas:null,steer:null};locked=false;resetAxes();finishRecord()}
window.addEventListener('blur',leave);window.addEventListener('pagehide',leave);
document.addEventListener('visibilitychange',()=>{if(document.hidden)leave()});
function tab(show){
 leave();el('show').hidden=!show;el('drive').hidden=show;
 el('showTab').classList.toggle('chosen',show);el('driveTab').classList.toggle('chosen',!show);
 el('modes').hidden=show;el('stop').hidden=!show;el('record').hidden=show;requestAnimationFrame(()=>{paint();preview()})
}
el('showTab').onclick=()=>tab(true);el('driveTab').onclick=()=>tab(false);
el('record').onclick=()=>{if(rec){finishRecord();return}rec={start:now(),frames:[[0,0,0]]};el('record').classList.add('on');note('正在录制驾驶动作')};
function setMode(next){
 if(held.gas!==null||held.steer!==null||session||arming||stops||rec){note('松开双手并结束录制后可换档');return}
 sport=next;document.body.classList.toggle('sport',sport);
 for(const [id,on] of [['normal',!sport],['sport',sport]])el(id).setAttribute('aria-pressed',String(on));
 note(sport?'急速 · 上限 90%':'普通 · 上限 60%');paint()
}
el('normal').onclick=()=>setMode(false);el('sport').onclick=()=>setMode(true);
function reverseFrames(frames){
 const end=frames.at(-1)[0],out=[];
 for(let i=frames.length-2;i>=0;i--)out.push([end-frames[i+1][0],frames[i][1],frames[i][2]]);
 out.push([end,0,0]);return out
}
function updateOrbit(){
 if(replay||arming)return;
 choices.orbit.frames=[[0,Number(el('hand').value)*Number(el('bend').value),.82],[Number(el('duration').value)*1000,0,0]];preview()
}
for(const id of ['duration','hand','bend'])el(id).onchange=updateOrbit;
function select(id){
 if(choices.saved)choices.backtrack={name:'原路返回',fast:choices.saved.fast,frames:reverseFrames(choices.saved.frames),reverse:true};
 el('backtrack').disabled=!choices.saved;el('orbitSettings').hidden=id!=='orbit';
 el('guide').textContent=({orbit:'操作者站在圆心，车头沿圆周切线摆放。先试大圈，再调整距离。',backtrack:'录制结束后保持车的位置和朝向，再运行以近似返回起点。',parade:'直进、双向弧线、倒车收尾。',saved:'重演上一次保存的驾驶操作。'})[id]||'按预设时序行驶，实际轨迹随地面变化。';
choice=id;el('saved').disabled=!choices.saved;el('saved').textContent=choices.saved?'我的绕桩':'暂无录制';
 for(const k of ['saved','backtrack','orbit','parade','snake','eight'])el(k).classList.toggle('chosen',k===id);
 el('playTitle').textContent=choices[id].name;el('progress').value=0;preview()}
for(const k of ['saved','backtrack','orbit','parade','snake','eight'])el(k).onclick=()=>{if(!replay&&!arming)select(k)};
el('play').onclick=async()=>{
 if(replay||arming){halt('已中止重演');return}
 if(await arm(!!choices[choice].fast)){replay={start:now(),i:0,reverse:choices[choice].reverse,frames:choices[choice].frames};el('play').textContent='中止重演';note('正在重演');tick()}
};
function tick(){
 if(rec)el('record').textContent='结束录制 '+stamp(now()-rec.start);
 if(race!==null)el('clock').textContent=stamp(now()-race);
 if(replay){const t=now()-replay.start,fs=replay.frames,duration=fs.at(-1)[0];
 el('progress').value=Math.min(1,t/duration);el('elapsed').textContent=stamp(Math.min(t,duration))+' / '+stamp(duration);
 if(t>=duration){halt('重演结束');return}
 while(replay.i+1<fs.length&&fs[replay.i+1][0]<=t)replay.i++;
 [,x,y]=fs[replay.i];preview()
 }
 if(session)pump()
}
function timerLabels(){el('best').textContent='最好成绩　'+(load('bearpi-best')?stamp(load('bearpi-best')):'—')}
el('timer').onclick=()=>{if(race===null){race=now();el('timer').textContent='结束计时';tab(false);el('raceEnd').hidden=false}
 else endRace()};
function endRace(){if(race===null)return;const t=now()-race;race=null;el('clock').textContent=stamp(t);
 const best=load('bearpi-best');if(!best||t<best)save('bearpi-best',t);timerLabels();
 el('timer').textContent='开始计时';el('raceEnd').hidden=true;tab(true)}
el('raceEnd').onclick=endRace;
function canvas(id){
 const c=el(id),r=c.getBoundingClientRect();if(!r.width||!r.height)return;
 const d=Math.min(devicePixelRatio||1,2);c.width=Math.round(r.width*d);c.height=Math.round(r.height*d);
 const g=c.getContext('2d');g.scale(d,d);return [g,r.width,r.height]
}
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
let roadFrame = 0, roadPhase = 0, lastRoadTime = 0, horizonCache;

function makeHorizon(w,h,d) {
 const layer=document.createElement('canvas');
 layer.width=Math.round(w*d);layer.height=Math.round(h*d);
 const ctx=layer.getContext('2d');
 ctx.scale(d,d);
 const sky=ctx.createLinearGradient(0,0,0,h);
 const colors=sport?['#191623','#4a2938','#c57553','#36212a','#171721']:['#0e192b','#1c354b','#488695','#182e40','#101c29'];
 [0,.18,.35,.40,1].forEach((p,i)=>sky.addColorStop(p,colors[i]));
 ctx.fillStyle=sky;ctx.fillRect(0,0,w,h);
 const sunX=w*.65,sunY=h*.24,r=Math.min(w*.13,h*.14);
 const glow=ctx.createRadialGradient(sunX,sunY,r*.2,sunX,sunY,r*2.9);
 glow.addColorStop(0,sport?'#ffae6d9c':'#a0eee86a');glow.addColorStop(1,'transparent');
 ctx.fillStyle=glow;ctx.fillRect(0,0,w,h*.39);
 ctx.save();ctx.beginPath();ctx.arc(sunX,sunY,r,0,2*Math.PI);ctx.clip();
 ctx.fillStyle=sport?'#ffce91':'#b3e6de';ctx.fillRect(sunX-r,sunY-r,r*2,r*1.1);
 for(let i=0;i<5;i++)ctx.fillRect(sunX-r,sunY+r*(.16+i*.19),2*r,r*(.14-i*.019));
 ctx.restore();
 // Two ridgelines keep the horizon legible behind the smaller controls.
 for(let layer=0;layer<2;layer++){
  ctx.beginPath();ctx.moveTo(0,h*.4);
  for(let i=0;i<=64;i++){
   const t=i/64,profile=Math.sin(i*.19+layer)*.38+Math.cos(i*.68+layer*5)*.16+Math.sin(i*1.53)*.085;
   ctx.lineTo(t*w,h*(.32+layer*.035+profile*.115));
  }
  ctx.lineTo(w,h*.41);ctx.lineTo(0,h*.41);ctx.closePath();
  ctx.fillStyle=layer?(sport?'#382531':'#1a3242'):(sport?'#784650':'#345667');ctx.fill();
 }
 return {layer,w,h,d,sport};
}

function renderRoad(time) {
 roadFrame=0;
 const road=el('road'),w=road.clientWidth,h=road.clientHeight;
 if(!w||!h||el('drive').hidden||document.hidden){lastRoadTime=0;return}
 const d=Math.min(devicePixelRatio||1,2),g=road.getContext('2d');
 if(!horizonCache||horizonCache.w!==w||horizonCache.h!==h||horizonCache.d!==d||horizonCache.sport!==sport){
  horizonCache=makeHorizon(w,h,d);
  road.width=horizonCache.layer.width;road.height=horizonCache.layer.height;
 }
 const moving=Math.abs(y)>.07&&!reduceMotion.matches;
 if(moving&&lastRoadTime&&time-lastRoadTime<32){roadFrame=requestAnimationFrame(renderRoad);return}
 if(moving&&lastRoadTime)roadPhase=(1+roadPhase+Math.sign(y)*Math.abs(y)*Math.min(80,time-lastRoadTime)*(sport?.0019:.0012))%1;
 lastRoadTime=moving?time:0;
 g.setTransform(d,0,0,d,0,0);g.drawImage(horizonCache.layer,0,0,w,h);
 const horizon=h*.39, ground=h-horizon;
 const cx=p=>w*(.5+x*.29*(1-p)*(1-p));
 const half=p=>w*(.045+.60*p), yy=p=>horizon+ground*p;
 const accent=sport?'#ffae65':'#67e6dd';
 function edge(side,offset=1){
  g.beginPath();
  for(let i=0;i<=20;i++){const p=i/20;g.lineTo(cx(p)+side*half(p)*offset,yy(p))}
 }
 g.beginPath();
 for(let i=0;i<=20;i++){const p=i/20;g.lineTo(cx(p)-half(p),yy(p))}
 for(let i=20;i>=0;i--){const p=i/20;g.lineTo(cx(p)+half(p),yy(p))}
 g.closePath();g.fillStyle=sport?'#231c27':'#172837';g.fill();
 for(const side of [-1,1]){
  edge(side);g.strokeStyle=sport?'#c8784b':'#458b91';g.lineWidth=1.8;g.stroke();
  edge(side,.92);g.strokeStyle=sport?'#67424a':'#31515e';g.lineWidth=.8;g.stroke();
 }
 for(let i=0;i<12;i++){
  const depth=(i+roadPhase)/12, p=depth*depth;
  const pos=cx(p),r=half(p);
  g.fillStyle=sport?'#ffab73':'#86e6e3';
  for(const side of [-1,1])g.fillRect(pos+side*r-1,yy(p),1+3*p,1+2*p);
  g.strokeStyle=sport?'#ffc48c18':'#a5e3ed18';g.lineWidth=1;
  g.beginPath();g.moveTo(0,yy(p));g.lineTo(pos-r,yy(p));g.moveTo(pos+r,yy(p));g.lineTo(w,yy(p));g.stroke();
  if(i%2===0){g.fillStyle='#bfd5d32b';g.fillRect(pos,yy(p),1+1.4*p,1+7*p)}
 }
 if(sport&&Math.abs(y)>.15){
  // Roadside reflections stretch with the throttle and sweep past in perspective.
  g.lineCap='round';
  for(let lane=0;lane<6;lane++){
   const side=lane%2?1:-1, offset=1.15+Math.floor(lane/2)*.32;
   const t=(roadPhase+lane*.37)%1, near=t*t;
   const far=Math.max(0,near-(.03+.19*near)*Math.abs(y));
   const ax=cx(far)+side*half(far)*offset,bx=cx(near)+side*half(near)*offset;
   const trail=g.createLinearGradient(ax,yy(far),bx,yy(near));
   trail.addColorStop(0,'#ff745300');trail.addColorStop(1,'#ffd39ca8');
   g.strokeStyle=trail;g.lineWidth=.7+near*2;g.beginPath();
   g.moveTo(ax,yy(far));g.lineTo(bx,yy(near));g.stroke();
  }
 }
 const car=el('carPhoto'),carH=car.offsetHeight;
 const mid=h*.66,start=mid-Math.sign(y)*carH*.41;
 if(Math.abs(y)>.07){
  const reverse=y<0;
  const endX=w*(.5+x*.26),endY=reverse?h*.965:h*.405;
  const path=new Path2D();
  path.moveTo(w*.5,start);path.quadraticCurveTo(w*.5,start+(endY-start)*.75,endX,endY);
  g.strokeStyle=accent+'30';g.lineWidth=Math.max(7,w*.022);g.stroke(path);
  g.save();g.setLineDash([4,4]);g.lineDashOffset=-roadPhase*8;
  g.strokeStyle=accent;g.lineWidth=Math.max(1.6,w*.005);g.stroke(path);g.restore();
  g.fillStyle=accent;g.beginPath();g.arc(endX,endY,3,0,Math.PI*2);g.fill();
  // Paired light trails maintain their origin at the two wheels.
  const sign=Math.sign(y),from=mid+sign*carH*.33,len=carH*(sport?.62:.32)*Math.abs(y);
  for(const side of [-1,1]){
   const ax=w/2+side*car.offsetWidth*.35;
   const fade=g.createLinearGradient(0,from,0,from+sign*len);
   fade.addColorStop(0,accent+'cc');fade.addColorStop(1,accent+'00');
   g.fillStyle=fade;g.beginPath();g.moveTo(ax-3,from);g.lineTo(ax+3,from);g.lineTo(ax+side*5-x*6,from+sign*len);g.fill();
  }
 }
 if(moving)roadFrame=requestAnimationFrame(renderRoad);
}
function refreshRoad(){
 if(!roadFrame&&!el('drive').hidden&&!document.hidden)roadFrame=requestAnimationFrame(renderRoad);
}
function paint(){
 const drive=el('drive'),gasPos=50-50*y,steerPos=50+50*x;
 el('gas').querySelector('i').style.top=gasPos+'%';
 el('steer').querySelector('i').style.left=steerPos+'%';
 for(const [name,pos] of [['gas',gasPos],['steer',steerPos]]){
  drive.style.setProperty('--'+name+'-start',Math.min(50,pos)+'%');
  drive.style.setProperty('--'+name+'-span',Math.abs(pos-50)+'%');
 }
 el('gasValue').textContent=Math.round(Math.abs(y)*100)+'%';
 el('steerValue').textContent=(Math.abs(x)<.005?'':x<0?'左 ':'右 ')+Math.round(Math.abs(x)*100)+'%';
 el('gear').textContent=Math.abs(y)<=.07?'N':y>0?'D':'R';
 refreshRoad();
}
function preview(){
 const cv=canvas('preview');if(!cv)return;
 const [g,w,h]=cv,fs=choices[choice].frames,end=fs.at(-1)[0];
 let px=0,py=0,angle=-Math.PI/2,i=0,points=[[0,0]];
 for(let t=0;t<end;t+=Math.max(50,end/500)){
 while(i+1<fs.length&&fs[i+1][0]<=t)i++;
 const [,s,v]=fs[i];if(Math.abs(v)>.07){const sign=choices[choice].reverse?-1:1;angle+=sign*s*Math.sign(v)*.065;px+=sign*v*Math.cos(angle);py+=sign*v*Math.sin(angle)}points.push([px,py])}
 const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),lo=Math.min(...xs),hi=Math.max(...xs),top=Math.min(...ys),bot=Math.max(...ys);
 const k=Math.min((w-20)/Math.max(1,hi-lo),(h-15)/Math.max(1,bot-top));
 g.strokeStyle='#75dfb3';g.lineWidth=2;g.setLineDash([4,4]);g.beginPath();for(const [a,b]of points)g.lineTo(w/2+(a-(lo+hi)/2)*k,h/2+(b-(top+bot)/2)*k);g.stroke();
 if(replay){const index=Math.min(points.length-1,Math.floor(el('progress').value*(points.length-1)));const [a,b]=points[index];g.fillStyle='#ffd27b';g.beginPath();g.arc(w/2+(a-(lo+hi)/2)*k,h/2+(b-(top+bot)/2)*k,4,0,2*Math.PI);g.fill()}
 if(!replay)el('elapsed').textContent='预计路线 · '+stamp(end);
}
select(choice);timerLabels();paint();window.addEventListener('resize',()=>{paint();preview()});
async function check(){if(session||arming||document.hidden)return;try{await api('status');state(true)}catch(e){state(false);note(e.message)}}
check();setInterval(check,2500);setInterval(tick,100);
