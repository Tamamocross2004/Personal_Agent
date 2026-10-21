from contextlib import contextmanager
from pathlib import Path
import os
import time


@contextmanager
def lease(path: Path, wait=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError('知识库锁不得为链接')
    stream=path.open('a+b')
    if path.stat().st_size==0: stream.write(b'0'); stream.flush()
    deadline=time.monotonic()+wait
    try:
        while True:
            stream.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic()>=deadline: raise ValueError('知识库正在使用，请稍后重试') from None
                time.sleep(.05)
        try: yield
        finally:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    finally: stream.close()
