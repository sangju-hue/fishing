const ID=/^[a-f0-9]{32}$/;
const origins=new Set(['https://sangju-hue.github.io','http://127.0.0.1:8000','http://localhost:8000']);
async function hash(s){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s)))].map(x=>x.toString(16).padStart(2,'0')).join('');}
async function authorized(req,env){
 const token=req.headers.get('Authorization')?.replace(/^Bearer /,'');
 if(!env.AGENT_TOKEN||!token)return false;
 const a=await hash(token),b=await hash(env.AGENT_TOKEN);let diff=0;for(let i=0;i<a.length;i++)diff|=a.charCodeAt(i)^b.charCodeAt(i);return diff===0;
}
function response(req,data,status=200){
 const headers={'Content-Type':'application/json','Cache-Control':'no-store','Vary':'Origin'};
 const origin=req.headers.get('Origin');if(origins.has(origin))headers['Access-Control-Allow-Origin']=origin;
 return new Response(JSON.stringify(data),{status,headers});
}
async function body(req){
 if(Number(req.headers.get('Content-Length')||0)>26000)throw Error('too-large');
 const reader=req.body?.getReader();if(!reader)throw Error('invalid-body');
 let total=0,chunks=[];
 while(true){const {value,done}=await reader.read();if(done)break;total+=value.length;if(total>26000){await reader.cancel();throw Error('too-large');}chunks.push(value);}
 const raw=new Uint8Array(total);let offset=0;for(const c of chunks){raw.set(c,offset);offset+=c.length;}
 return JSON.parse(new TextDecoder().decode(raw));
}
async function fetchHandler(req,env){
 if(new URL(req.url).protocol!=='https:')return response(req,{error:'HTTPS 연결이 필요합니다'},400);
 const path=new URL(req.url).pathname,now=Math.floor(Date.now()/1000),origin=req.headers.get('Origin');
 if(path==='/health'&&req.method==='GET')return response(req,{service:'fishing-alert-relay',version:1});
 if(req.method==='OPTIONS'){
  if(!origins.has(origin))return response(req,{error:'허용되지 않은 연결'},403);
  return new Response(null,{status:204,headers:{'Access-Control-Allow-Origin':origin,'Access-Control-Allow-Methods':'GET, POST, OPTIONS','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Max-Age':'86400','Vary':'Origin'}});
 }
 if(path.startsWith('/agent/')){
  if(!await authorized(req,env))return response(req,{error:'인증 실패'},403);
  if(path==='/agent/requests'&&req.method==='GET'){
   const r=await env.DB.prepare('SELECT id, message FROM requests WHERE result IS NULL AND created_at > ? ORDER BY created_at LIMIT 20').bind(now-86400).all();
   return response(req,{requests:r.results});
  }
  if(path==='/agent/results'&&req.method==='POST'){
   const p=await body(req);if(!ID.test(p.request_id)||typeof p.ok!=='boolean')return response(req,{error:'처리 결과 형식 오류'},400);
   const result={request_id:p.request_id,ok:p.ok};if(!p.ok)result.error=String(p.error||'신청 처리 오류').slice(0,160);
   await env.DB.prepare('UPDATE requests SET result = ?, message = NULL, done_at = ? WHERE id = ? AND result IS NULL').bind(JSON.stringify(result),now,p.request_id).run();
   return response(req,{ok:true});
  }
  return response(req,{error:'없음'},404);
 }
 if(!origins.has(origin))return response(req,{error:'허용되지 않은 연결'},403);
 if(path==='/requests'&&req.method==='POST'){
  const p=await body(req);
  if(!ID.test(p.request_id)||!ID.test(p.receipt_token)||typeof p.message!=='string'||p.message.length>24000||!/^fish[12]:[A-Za-z0-9+/=.]+$/.test(p.message))return response(req,{error:'신청 형식 오류'},400);
  const tokenHash=await hash(p.receipt_token),messageHash=await hash(p.message);
  const old=await env.DB.prepare('SELECT token_hash, message_hash, created_at FROM requests WHERE id = ?').bind(p.request_id).first();
  if(old){
   if(old.token_hash!==tokenHash||old.message_hash!==messageHash)return response(req,{error:'신청 식별값 충돌'},409);
   return response(req,{request_id:p.request_id},202);
  }
  const bucket=Math.floor(now/3600),key=await hash((env.AGENT_TOKEN||'')+':'+req.headers.get('CF-Connecting-IP')+':'+bucket);
  const rate=await env.DB.prepare('INSERT INTO rate_limits(key,count,bucket) VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1 RETURNING count').bind(key,bucket).first();
  if(rate.count>60)return response(req,{error:'신청 횟수가 많습니다. 잠시 후 다시 시도하세요.'},429);
  const size=await env.DB.prepare('SELECT COUNT(*) AS count FROM requests WHERE result IS NULL AND created_at > ?').bind(now-86400).first();
  if(size.count>=300)return response(req,{error:'신청 대기열이 가득 찼습니다. 잠시 후 다시 시도하세요.'},503);
  const inserted=await env.DB.prepare('INSERT OR IGNORE INTO requests(id,token_hash,message_hash,message,created_at) VALUES (?,?,?,?,?)').bind(p.request_id,tokenHash,messageHash,p.message,now).run();
  if(!inserted.meta.changes){
   const raced=await env.DB.prepare('SELECT token_hash,message_hash FROM requests WHERE id=?').bind(p.request_id).first();
   if(raced.token_hash!==tokenHash||raced.message_hash!==messageHash)return response(req,{error:'신청 식별값 충돌'},409);
  }
  return response(req,{request_id:p.request_id},202);
 }
 const match=path.match(/^\/receipts\/([a-f0-9]{32})$/);
 if(match&&req.method==='GET'){
  const token=req.headers.get('Authorization')?.replace(/^Bearer /,'');if(!ID.test(token||''))return response(req,{error:'처리 확인 인증 실패'},403);
  const r=await env.DB.prepare('SELECT token_hash,result,created_at FROM requests WHERE id=?').bind(match[1]).first();
  if(!r||r.token_hash!==await hash(token))return response(req,{error:'신청 정보를 찾지 못했습니다'},404);
  if(r.result)return response(req,{done:true,...JSON.parse(r.result)});
  if(r.created_at<=now-86400)return response(req,{done:true,request_id:match[1],ok:false,error:'신청 대기 시간이 지났습니다. 다시 신청해 주세요.'});
  return response(req,{done:false,request_id:match[1]});
 }
 return response(req,{error:'없음'},404);
}
export default {
 async fetch(req,env){try{return await fetchHandler(req,env);}catch(e){return response(req,{error:e.message==='too-large'?'신청 크기 초과':'중계 요청 처리 실패'},e instanceof SyntaxError||['too-large','invalid-body'].includes(e.message)?400:503);}},
 async scheduled(event,env){const now=Math.floor(Date.now()/1000);await env.DB.batch([env.DB.prepare('DELETE FROM requests WHERE created_at < ?').bind(now-172800),env.DB.prepare('DELETE FROM rate_limits WHERE bucket < ?').bind(Math.floor(now/3600)-2)]);}
};
