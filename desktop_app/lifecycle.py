"""父进程意外退出时，只清理当前服务及其子进程。"""
import os
import time


def watch_parent(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if handle:
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            kernel.CloseHandle(handle)
        import subprocess
        subprocess.run(['taskkill', '/PID', str(os.getpid()), '/T', '/F'],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        while os.getppid() == pid:
            time.sleep(.5)
        # 与桌面服务同组的任务可能另开进程组；逐层的父进程监视器继续清理。
        os._exit(130)
