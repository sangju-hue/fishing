const assert=require('assert'),fs=require('fs'),crypto=require('crypto'),{JSDOM}=require('../node_modules/jsdom');
const source=fs.readFileSync('index.html','utf8'),part=source.slice(source.indexOf("let ntfyReplyTopic='"),source.indexOf('function renderNtfyAdmin(')).replace('45000','25');
const w=new JSDOM('',{url:'https://fixture.test/',runScripts:'outside-only'}).window;
Object.defineProperty(w.crypto,'subtle',{value:crypto.webcrypto.subtle});w.TextEncoder=TextEncoder;
const pair=crypto.generateKeyPairSync('rsa',{modulusLength:3072}),public_key=pair.publicKey.export({type:'spki',format:'der'}).toString('base64');
let stream,mode='ok',sent=[];
w.EventSource=class{constructor(){stream=this}close(){}};
function decrypt(message){
 const rsa=raw=>crypto.privateDecrypt({key:pair.privateKey,oaepHash:'sha256'},Buffer.from(raw,'base64'));
 if(message.startsWith('fish1:'))return JSON.parse(rsa(message.slice(6)));
 const [wrapped,iv,ciphertext,mac]=message.slice(6).split('.').map(x=>Buffer.from(x,'base64')),secret=rsa(wrapped.toString('base64'));
 assert(crypto.timingSafeEqual(crypto.createHmac('sha256',secret.subarray(32)).update(Buffer.concat([iv,ciphertext])).digest(),mac));
 const aes=crypto.createDecipheriv('aes-256-cbc',secret.subarray(0,32),iv);return JSON.parse(Buffer.concat([aes.update(ciphertext),aes.final()]));
}
w.fetch=async(url,options={})=>{
 if(options.method==='POST'){
  const p=decrypt(options.body);sent.push(p);assert.equal(p.reply_topic,'fishing-reply-'+'b'.repeat(32));
  if(mode!=='timeout')stream.onmessage({data:JSON.stringify({event:'message',message:JSON.stringify({request_id:p.request_id,ok:mode==='ok',error:'topic collision'})})});
  return {ok:true};
 }
 return {ok:true,text:async()=>''};
};
w.eval(`let ntfyLocal=false,ntfyConfig=${JSON.stringify({server:'https://example.test',inbox:'fake',supports_receipts:true,public_key})};function ntfyRandom(){return [...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('')};`+part+`;ntfyReplyTopic='fishing-reply-'+ 'b'.repeat(32);window.request=ntfyRequest;`);
(async()=>{
 const p={action:'add',owner:'a'.repeat(32),topic:'test',date:'2026-10-09',bid:1,bids:Array.from({length:317},(_,i)=>i+1),ports:['*'],label:'fixture'};
 assert.equal(await w.request(p),true,'confirm only after Mac receipt');assert.equal(sent[0].bids.length,317,'large hybrid encrypted selection survives');
 mode='deny';await assert.rejects(w.request({...p,topic:'collision'}),/topic collision/);
 mode='timeout';await assert.rejects(w.request(p),/再|맥/);const pending=sent.at(-1).request_id;
 mode='ok';assert.equal(await w.request(p),true);assert.equal(sent.at(-1).request_id,pending,'retry uses same id after unknown outcome');
 w.close();console.log('PASS: hybrid encrypted large request, confirmed success/denial, receipt before waiter, timeout retry with same request ID');
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
