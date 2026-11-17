const {chromium}=require('playwright'),fs=require('fs'),path=require('path');
(async()=>{
 const base=path.resolve(__dirname,'../web');
 const b=await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE,args:['--no-proxy-server'],headless:true});
 try{
 const p=await b.newPage({viewport:{width:844,height:390},deviceScaleFactor:1.25,isMobile:true,hasTouch:true});
 let commands=[];const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.route('http://console.test/**', async r=>{
  const route=new URL(r.request().url()).pathname;
  if(route==='/control'){
   const cmd=r.request().postData();commands.push(cmd);
   return r.fulfill({json:cmd.startsWith('arm')?{session:'demo'}:{version:'v12'}});
  }
  let f=route==='/'?'index.html':route.slice(1),data=fs.readFileSync(path.join(base,f),'utf8');
  if(f==='app.js')data=data.slice(0,data.indexOf('async function api('))+fs.readFileSync(path.join(base,'board_transport.js'),'utf8')+'\n'+data.slice(data.indexOf('function state('));
  return r.fulfill({contentType:({'index.html':'text/html; charset=utf-8','app.js':'text/javascript','style.css':'text/css'})[f],body:data});
 });
 await p.goto('http://console.test/');await p.waitForSelector('#status.online');
 await p.click('#sport');
 const c=await p.context().newCDPSession(p);
 async function pt(id,sel,rx,ry){const r=await p.locator(sel).boundingBox();return {id,x:r.x+r.width*rx,y:r.y+r.height*ry}}
 const gas=await pt(1,'#gas .track',.5,.065),steer=await pt(2,'#steer .track',.81,.5);
 const touch=(type,touchPoints)=>c.send('Input.dispatchTouchEvent',{type,touchPoints});
 await touch('touchStart',[gas]);await touch('touchStart',[gas,steer]);await p.waitForTimeout(160);
 if(!commands.includes('arm-fast')||!commands.some(c=>c.startsWith('drive ')))throw Error('sport controls');
 await touch('touchEnd',[]);await p.waitForTimeout(140);
 if(!commands.includes('stop')||await p.locator('#gear').innerText()!=='N')throw Error('release');
 await p.click('#showTab');await p.click('#eight');await p.click('#play');await p.waitForTimeout(180);await p.click('#stop');
 await p.click('#driveTab');await p.click('#normal');
 for(const [w,h] of [[667,320],[390,844],[844,390]]){
 await p.setViewportSize({width:w,height:h});await p.waitForTimeout(70);
 if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1||document.documentElement.scrollHeight>innerHeight+1))throw Error('overflow '+w);
 }
 await p.emulateMedia({reducedMotion:'reduce'});await p.waitForTimeout(100);
  await touch('touchStart',[gas]);await p.waitForTimeout(135);
 const reduced=()=>p.locator('#road').evaluate(el=>el.toDataURL());
 const first=await reduced();await p.waitForTimeout(170);
 if(first!==await reduced())throw Error('reduced motion changed');
 await touch('touchEnd',[]);await p.waitForTimeout(120);
 const stopped=await reduced();await p.waitForTimeout(140);
 if(stopped!==await reduced())throw Error('idle animation continued');
 console.log('PASS: reduced-motion and idle frames static');if(errors.length)throw Error(errors.join('\n'));console.log('PASS: board transport, boost, two fingers, release, replay stop, 3 viewports');
 }finally{await b.close()}
})().catch(e=>{console.error(e);process.exitCode=1})
