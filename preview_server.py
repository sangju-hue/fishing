#!/usr/bin/env python3
"""Loopback preview: explicitly public assets only; never serve project/private files."""
import argparse
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,unquote

BASE=Path(__file__).resolve().parent
PUBLIC_DATA={'boats.json','status.json','status_homepages.json','site_health.json','site_health_sunsang24.json','scrape_audit_current.json','ntfy_public.json','ntfy_topics.json','snapshot.json'}

class PublicHandler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(BASE),**kwargs)
    def send_head(self):
        path=unquote(urlsplit(self.path).path)
        allowed=path in ('/','/index.html','/booking_routes.js','/alert-relay.js','/bada.css','/theme.js','/bada-ui.js') or path in ('/design/assets/Ocellated_octopus.jpg','/design/assets/Sepia_esculenta_Kamo.jpg','/design/assets/Sebastes_schlegelii_by_OpenCage_2.jpg','/design/assets/Trichiurus_lepturus_by_OpenCage.jpg','/design/assets/octopus.jpg') or path.startswith('/data/') and path[6:] in PUBLIC_DATA
        if not allowed or any(part.startswith('.') for part in path.split('/') if part):self.send_error(404);return None
        full=(BASE/path.lstrip('/')).resolve()
        if full!=BASE and BASE not in full.parents:self.send_error(404);return None
        self.path=path
        return super().send_head()
    def list_directory(self,path):self.send_error(404);return None
    def end_headers(self):self.send_header('Cache-Control','no-cache');super().end_headers()
    def log_message(self,*args):pass

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8000);args=p.parse_args()
    ThreadingHTTPServer(('127.0.0.1',args.port),PublicHandler).serve_forever()
if __name__=='__main__':main()
