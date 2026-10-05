"""Activate only a deployed/authenticated relay. Never print the agent secret."""
import json,os,secrets,subprocess,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from urllib.parse import urlsplit
base=Path(__file__).resolve().parent.parent
url=sys.argv[1].rstrip('/')
u=urlsplit(url)
if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.workers.dev') or u.username or u.password or u.query or u.fragment or u.path:raise ValueError('Invalid relay URL')
path=base/'.ntfy/relay.json';staging=base/'.ntfy/relay_setup.json'
source=path if path.exists() else staging
prior=json.loads(source.read_text()) if source.exists() else {}
token=prior.get('agent_token') or secrets.token_hex(32)
staging.parent.mkdir(mode=0o700,exist_ok=True)
fd=os.open(str(staging),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump({'url':url,'agent_token':token},f)
os.chmod(staging,0o600)
# Store the secret through stdin, not command-line arguments.
subprocess.run([str(base/'cloudflare-relay/node_modules/.bin/wrangler'),'secret','put','AGENT_TOKEN'],cwd=base/'cloudflare-relay',input=token+'\n',text=True,check=True)
req=urllib.request.Request(url+'/agent/requests',headers={'Authorization':'Bearer '+token,'User-Agent':'BadajariMac/1.0'})
for attempt in range(180):
    try:
        with urllib.request.urlopen(req,timeout=15) as response:data=json.load(response)
        if not isinstance(data.get('requests'),list):raise ValueError('Unexpected relay response')
        break
    except OSError:
        if attempt==179:raise
        time.sleep(5)
path.parent.mkdir(mode=0o700,exist_ok=True)
fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:json.dump({'url':url,'agent_token':token},f)
os.chmod(path,0o600)
staging.unlink(missing_ok=True)
print('Mac private relay configuration saved; authenticated endpoint verified.')
