#!/usr/bin/env python3
"""Install the loopback-only public preview as a login LaunchAgent."""
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import time

BASE=Path(__file__).resolve().parent
LABEL='com.sam.fishing-preview'

def main():
    domain=f'gui/{os.getuid()}'
    directory=Path.home()/'Library'/'LaunchAgents'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(LABEL+'.plist')
    config={'Label':LABEL,'ProgramArguments':[sys.executable,str(BASE/'preview_server.py'),'--port','8000'],
            'WorkingDirectory':str(BASE),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
            'StandardOutPath':str(BASE/'preview.log'),'StandardErrorPath':str(BASE/'preview.log')}
    subprocess.run(['launchctl','bootout',domain+'/'+LABEL],capture_output=True)
    result=subprocess.run(['lsof','-t','-iTCP:8000','-sTCP:LISTEN'],capture_output=True,text=True)
    for raw in set(result.stdout.split()):
        pid=int(raw)
        command=subprocess.check_output(['ps','-p',raw,'-o','command='],text=True)
        cwd=subprocess.check_output(['lsof','-a','-p',raw,'-d','cwd','-Fn'],text=True)
        if 'preview_server.py' not in command or 'n'+str(BASE) not in cwd.splitlines():
            raise RuntimeError('8000 포트를 다른 프로그램이 사용 중입니다. 해당 프로그램은 중지하지 않았습니다.')
        os.kill(pid,signal.SIGTERM)
    # Give the previous listener time to close before launchd binds the same port.
    for _ in range(20):
        result=subprocess.run(['lsof','-t','-iTCP:8000','-sTCP:LISTEN'],capture_output=True)
        if not result.stdout:break
        time.sleep(.1)
    with path.open('wb') as stream:plistlib.dump(config,stream)
    os.chmod(path,0o600)
    subprocess.run(['launchctl','bootstrap',domain,str(path)],check=True)
    print('로컬 웹 서버 자동 실행 등록 완료: http://127.0.0.1:8000/ (맥 로그인 시 시작)')

if __name__=='__main__':main()
