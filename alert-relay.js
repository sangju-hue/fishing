/* Registration transport: encrypted requests only; ntfy remains phone delivery. */
window.badaAlertRelay={async open(data,key){
 const secret=Uint8Array.from(key.match(/../g),x=>parseInt(x,16));
 const [iv,cipher,mac]=data.split('.').map(x=>Uint8Array.from(atob(x),c=>c.charCodeAt(0)));
 const signed=new Uint8Array(iv.length+cipher.length);signed.set(iv);signed.set(cipher,iv.length);
 const hmac=await crypto.subtle.importKey('raw',secret.slice(32),{name:'HMAC',hash:'SHA-256'},false,['verify']);
 if(!await crypto.subtle.verify('HMAC',hmac,mac,signed))throw Error('설정 응답 검증 실패');
 const aes=await crypto.subtle.importKey('raw',secret.slice(0,32),{name:'AES-CBC'},false,['decrypt']);
 return JSON.parse(new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-CBC',iv},aes,cipher)));
},async submit({url,requestId,receiptToken,message}){
 const endpoint=new URL(url);
 if(endpoint.protocol!=='https:'||!endpoint.hostname.endsWith('.workers.dev')||endpoint.username||endpoint.password||endpoint.search||endpoint.hash||!['','/'].includes(endpoint.pathname))throw Error('알림 중계 주소 오류');
 const base=url.replace(/\/$/,''),deadline=Date.now()+90000;
 async function call(path,options={}){
  const r=await fetch(base+path,{...options,cache:'no-store',signal:AbortSignal.timeout(10000)});
  const data=await r.json();if(!r.ok)throw Error(data.error||'알림 중계 연결 실패 ('+r.status+')');return data;
 }
 await call('/requests',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId,receipt_token:receiptToken,message})});
 while(Date.now()<deadline){
  const result=await call('/receipts/'+requestId,{headers:{Authorization:'Bearer '+receiptToken}});
  if(result.done)return result;
  await new Promise(resolve=>setTimeout(resolve,2000));
 }
 throw Error('맥의 처리 확인을 기다리는 중입니다. 같은 작업을 다시 누르면 결과를 재확인합니다.');
}};
