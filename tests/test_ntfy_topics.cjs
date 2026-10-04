const assert=require('assert'),fs=require('fs'),{JSDOM}=require('../node_modules/jsdom');
const source=fs.readFileSync('index.html','utf8');
const funcs=source.slice(source.indexOf('function ntfyShowTopics('),source.indexOf('function ntfyRenderMine('));
function page(){
 const w=new JSDOM('<div id="ntfyTopics"></div><div id="ntfyTopicsTime"></div>',{runScripts:'outside-only'}).window;
 const streams=[];w.EventSource=class{constructor(url){this.url=url;streams.push(this)}close(){this.closed=true}};
 w.eval("function escapeHtml(s){return String(s)};let ntfyUsedTopics=[],ntfyTopicsVersion=0,ntfyTopicStream=null,ntfyConfig={server:'https://ntfy.sh',inbox:'test'},ntfyLocal=false;"+funcs+";ntfyConnectTopics();ntfyConnectTopics();");
 assert.equal(streams.length,1,'one connection per page');assert(streams[0].url.endsWith('/test-topics/sse?since=latest'));
 return {w,stream:streams[0],emit(topics,time){streams[0].onmessage({data:JSON.stringify({event:'message',message:JSON.stringify({topics,updated_at:time})})})},text(){return w.document.querySelector('#ntfyTopics').textContent}};
}
const a=page(),b=page();
for(const p of [a,b]){p.emit(['fish-a'],'2026-10-04T10:00:00Z');assert.equal(p.text(),'fish-a');p.emit(['fish-b'],'2026-10-04T10:00:01Z');assert.equal(p.text(),'fish-b');p.emit([],'2026-10-04T10:00:02Z');assert(p.text().includes('없습니다'));p.emit(['stale'],'2026-10-04T10:00:00Z');assert(!p.text().includes('stale'));p.stream.onerror();assert(p.w.document.querySelector('#ntfyTopicsTime').textContent.includes('재연결'));p.emit(['reconnected'],'2026-10-04T10:00:03Z');assert.equal(p.text(),'reconnected');p.w.close()}
assert(!source.includes('setInterval(()=>{if(!document.hidden)ntfyRefreshTopics()}'));
console.log('PASS: two pages SSE add/edit/delete, reconnect snapshot, stale response protection, one connection, no topic polling');
