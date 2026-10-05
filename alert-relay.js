/* Registration transport: encrypted requests only; ntfy remains phone delivery. */
window.badaAlertRelay={async submit({url,requestId,receiptToken,message}){
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
