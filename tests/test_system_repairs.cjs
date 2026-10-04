const assert=require('assert'),fs=require('fs'),{JSDOM}=require('../node_modules/jsdom');
const source=fs.readFileSync('index.html','utf8');
const current=Array.from({length:13},(_,i)=>({bid:i+1,name:'배'+i,port:'남항유어선부두',channels:{}}));
const w=new JSDOM('<select id="portFilter"></select><div class="snapshot"><strong></strong></div><div id="matrixWrap"></div>',{runScripts:'outside-only',pretendToBeVisual:true}).window;
let version=1,fail=false,parseFailure=false;const requests=[];
w.console.error=()=>{};
w.fetch=async(raw,options={})=>{
 const path=String(raw).split('?')[0];requests.push(path);
 if(options.method==='HEAD')return {ok:true,headers:{get:k=>k==='ETag'?(path==='data/boats.json'?'version'+version:'stable'):''}};
 if(fail&&path==='data/boats.json')throw Error('offline');
 const data=path==='data/boats.json'?{boats:version===1?current.slice(0,12):current}:path==='data/site_health.json'?{sites:{}}:{};
 return {ok:true,json:async()=>{if(parseFailure&&path==='data/status_homepages.json')throw Error('truncated JSON');return JSON.parse(JSON.stringify(data))}};
};
const loader=source.slice(source.indexOf('let BOATS=[];'),source.indexOf('function currentSeason('));
const normal=source.slice(source.indexOf('const PORT_ALIASES='),source.indexOf('// Preferred ports first;'));
const init=source.slice(source.indexOf('const smallPorts='),source.indexOf('function initRangePorts('));
const refresh=source.slice(source.indexOf('let dataSignature='),source.indexOf('function refreshCatalogSelectors('));
w.eval('const $=s=>document.querySelector(s);function escapeHtml(v){return String(v)};function renderMatrix(){};function renderCards(){};function renderAlertBar(){};function refreshCatalogSelectors(){};'+loader+normal+init+refresh+`;window.test={loadData,initPorts,checkForUpdates,setStamp:async()=>{dataSignature=await dataStamp()},count:()=>BOATS.length,choose:(sun,hp,bid,date)=>{observationCache.clear();SUNSANG_STATUS_BY_ID=sun;HOMEPAGE_STATUS_BY_ID=hp;return observationFor({bid},date)}};`);
(async()=>{
 await w.test.loadData();w.test.initPorts();await w.test.setStamp();assert.equal(w.test.count(),12);
 version=2;fail=true;await w.test.checkForUpdates();assert.equal(w.test.count(),12);
 fail=false;await w.test.checkForUpdates();assert.equal(w.test.count(),13,'retry same HEAD after transient GET failure');
 version=1;parseFailure=true;await assert.rejects(w.test.loadData());assert.equal(w.test.count(),13,'failed JSON never commits partially loaded catalog');parseFailure=false;
 const ds='2026-10-09',sun={[ds]:{'1':{boat_id:1,status:'full',checked_at:'2026-10-04T10:00:00+09:00'}}},hp={[ds]:{'1':{boat_id:1,status:'available',remaining:4,checked_at:'2026-10-04T11:00:00+09:00'}}};
 assert.equal(w.test.choose(sun,hp,1,ds).remaining,4);hp[ds]['1'].boat_id=2;assert.equal(w.test.choose(sun,hp,1,ds).status,'full');
 w.close();console.log('PASS: retry after GET failure, atomic snapshot, latest source and wrong-boat rejection');
})().catch(e=>{console.error(e);w.close();process.exitCode=1});
