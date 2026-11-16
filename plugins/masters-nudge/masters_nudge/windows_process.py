"""Windows Provider processes belong to a kill-on-close job at creation.

JOB_LIST assigns ownership inside CreateProcessW, so killing the caller cannot
leave a process between launch and job assignment. HANDLE_LIST limits inherited
handles to the three streams; the job itself is never inherited.
"""
import ctypes
from ctypes import wintypes
import msvcrt
import os
import subprocess


class _BasicLimits(ctypes.Structure):
    _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64),
                ("Flags", wintypes.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("Priority", wintypes.DWORD),
                ("Scheduling", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("Basic", _BasicLimits), ("Io", _IoCounters),
                ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR),
                ("desktop", wintypes.LPWSTR), ("title", wintypes.LPWSTR),
                *[(name, wintypes.DWORD) for name in
                  ("x", "y", "xsize", "ysize", "xchars", "ychars", "fill", "flags")],
                ("show", wintypes.WORD), ("reserved_size", wintypes.WORD),
                ("reserved_bytes", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
                ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
                ("pid", wintypes.DWORD), ("tid", wintypes.DWORD)]


class WindowsProcessJob:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "GetCurrentProcess": ([], wintypes.HANDLE),
            "DuplicateHandle": ([wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                 ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.BOOL),
            "InitializeProcThreadAttributeList": ([ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            "UpdateProcThreadAttribute": ([ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                           ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL),
            "DeleteProcThreadAttributeList": ([ctypes.c_void_p], None),
            "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo)], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = args, result
        self.process = None
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def start(self, command, *, stdin, stdout, stderr, environment, cwd, shell=False):
        text = command if isinstance(command, str) else subprocess.list2cmdline(command)
        if shell:
            text = f'{os.environ.get("COMSPEC", "cmd.exe")} /c "{text}"'
        self.command = command
        duplicate_handles = []
        attributes = None
        try:
            with open(os.devnull, "rb") as null:
                for stream in (stdin or null, stdout, stderr):
                    duplicate = wintypes.HANDLE()
                    caller = self.kernel.GetCurrentProcess()
                    if not self.kernel.DuplicateHandle(caller, msvcrt.get_osfhandle(stream.fileno()),
                                                       caller, ctypes.byref(duplicate), 0, True, 2):
                        raise ctypes.WinError(ctypes.get_last_error())
                    duplicate_handles.append(duplicate.value)
            size = ctypes.c_size_t()
            self.kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            attributes = ctypes.cast(buffer, ctypes.c_void_p)
            if not self.kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
                attributes = None
                raise ctypes.WinError(ctypes.get_last_error())
            jobs = (wintypes.HANDLE * 1)(self.handle)
            handles = (wintypes.HANDLE * 3)(*duplicate_handles)
            for key, value in ((0x2000D, jobs), (0x20002, handles)):
                if not self.kernel.UpdateProcThreadAttribute(attributes, 0, key, value,
                                                            ctypes.sizeof(value), None, None):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup = _StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = duplicate_handles
            startup.attributes = attributes
            info = _ProcessInfo()
            env = ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in
                                                        sorted(environment.items(), key=lambda item: item[0].upper())) + "\0")
            if not self.kernel.CreateProcessW(None, ctypes.create_unicode_buffer(text), None, None,
                                             True, 0x08000000 | 0x00080000 | 0x400,
                                             env, cwd, ctypes.byref(startup), ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.process, self.pid, self.returncode = info.process, info.pid, None
            self.kernel.CloseHandle(info.thread)
            return self
        finally:
            if attributes is not None:
                self.kernel.DeleteProcThreadAttributeList(attributes)
            for handle in duplicate_handles:
                self.kernel.CloseHandle(handle)

    def wait(self, timeout=None):
        milliseconds = 0xFFFFFFFF if timeout is None else min(0xFFFFFFFE, max(0, int(timeout * 1000)))
        result = self.kernel.WaitForSingleObject(self.process, milliseconds)
        if result == 258:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(self.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = code.value
        return self.returncode

    def kill(self):
        if not self.kernel.TerminateProcess(self.process, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.process:
            self.kernel.CloseHandle(self.process)
            self.process = None
