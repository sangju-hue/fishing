#!/usr/bin/env python3
"""Install the existing fishing launch agent as a single Mac scheduler."""
import os
import plistlib
import subprocess
import sys
from collect_homepages import BASE

def main():
    label='com.sam.fishing-scrape';domain=f'gui/{os.getuid()}'
    directory=os.path.expanduser('~/Library/LaunchAgents');os.makedirs(directory,exist_ok=True)
    path=os.path.join(directory,label+'.plist')
    config={'Label':label,'ProgramArguments':[sys.executable,os.path.join(BASE,'mac_scheduler.py')],
            'WorkingDirectory':BASE,'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
            'EnvironmentVariables':{'PATH':os.path.dirname(sys.executable)+':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'},
            'StandardOutPath':os.path.join(BASE,'scrape.log'),'StandardErrorPath':os.path.join(BASE,'scrape.log')}
    subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
    if os.path.exists(path) and not os.path.exists(path+'.backup'):
        with open(path,'rb') as f:old=f.read()
        with open(path+'.backup','wb') as f:f.write(old)
    with open(path,'wb') as f:plistlib.dump(config,f)
    subprocess.run(['launchctl','bootstrap',domain,path],check=True)
    print('맥 수집기 설치 완료. 기본 5분, 웹페이지에서 주기 변경 가능.')

if __name__=='__main__':main()
