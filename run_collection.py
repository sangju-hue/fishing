"""Prevent concurrent collection, including manually invoked fishing.sh."""
import fcntl
import os
import subprocess
import sys
from collect_homepages import BASE

def main():
    with open(os.path.join(BASE,'.scrape.lock'),'a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('이미 수집 중입니다. 중복 실행을 건너뜁니다.',flush=True);return 0
        for script in ('scrape_sunsang24.py','scrape_homepages.py','push_to_github.py'):
            args=['--incremental'] if script.startswith('scrape_') else []
            result=subprocess.run([sys.executable,os.path.join(BASE,script),*args],cwd=BASE)
            if result.returncode:return result.returncode
    return 0

if __name__=='__main__':sys.exit(main())
