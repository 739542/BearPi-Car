async function api(action,body={}) {
 let command=action==='arm' && body.fast ? 'arm-fast' : action;
 if(action==='drive'){
   const {x,y,session,seq}=body,rad=Math.hypot(x,y);
   let l=0,r=0;
   if(Math.abs(y)>.07 && rad>.10){
     let a=y+x,b=y-x;
     const gain=30*Math.min(1,(rad-.10)/.90)/Math.max(Math.abs(a),Math.abs(b));
     if(y<0){const band=Math.abs(x)*Math.tan(Math.PI/18);
       if(-y>=band)[a,b]=[b,a];else {const f=-y/band;[a,b]=[a+(b-a)*f,b+(a-b)*f]}}
     l=Math.round(gain*a);r=Math.round(gain*b);
   }
   if(body.reverse){l=-l;r=-r}
   command='drive '+session+' '+seq+' '+l+' '+r;
 }
 const ctrl=new AbortController(),timer=setTimeout(()=>ctrl.abort(),1100);
 try {
   const res=await fetch('/control',{method:'POST',headers:{'X-Car-Control':'1'},body:command,signal:ctrl.signal});
   const result=await res.json();if(!res.ok)throw Error(result.error);return result;
 }finally{clearTimeout(timer)}
}
