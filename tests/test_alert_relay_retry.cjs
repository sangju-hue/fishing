const assert=require('assert'),fs=require('fs'),crypto=require('crypto'),{JSDOM}=require('../node_modules/jsdom');
const source=fs.readFileSync('index.html','utf8'),part=source.slice(source.indexOf("let ntfyReplyTopic='"),source.indexOf('function renderNtfyAdmin('));
const w=new JSDOM('',{url:'https://sangju-hue.github.io',runScripts:'outside-only'}).window;
Object.defineProperty(w.crypto,'subtle',{value:crypto.webcrypto.subtle});w.TextEncoder=TextEncoder;
const pair=crypto.generateKeyPairSync('rsa',{modulusLength:3072}),public_key=pair.publicKey.export({type:'spki',format:'der'}).toString('base64');
let calls=[];w.badaAlertRelay={async submit(p){calls.push(p);if(calls.length===1)throw Error('timeout');return {done:true,ok:true}}};
w.EventSource=class{constructor(){throw Error('must not open ntfy SSE')}};w.fetch=()=>{throw Error('must not POST to ntfy')};
w.eval(`let ntfyCanConfigure=true,ntfyLocal=false,ntfyConfig=${JSON.stringify({relay_url:'https://fixture.workers.dev',supports_receipts:true,public_key})};function ntfyRandom(){return [...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('')};`+part+';window.request=ntfyRequest;');
(async()=>{const p={action:'add',owner:'a'.repeat(32),topic:'fishing-fixture',date:'2026-10-09',bid:1};await assert.rejects(w.request(p),/timeout/);assert.equal(await w.request(p),true);assert.deepEqual(calls[0],calls[1]);assert.equal(w.localStorage.getItem('fishingNtfyPending'),null);w.close();console.log('PASS: relay retry preserves ciphertext and credentials, no ntfy command/receipt transport');})().catch(e=>{console.error(e);w.close();process.exitCode=1});
