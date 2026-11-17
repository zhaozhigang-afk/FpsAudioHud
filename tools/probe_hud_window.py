
import ctypes, ctypes.wintypes as wt, subprocess, sys

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowLongW = user32.GetWindowLongW
GetWindowRect = user32.GetWindowRect
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowVisible = user32.IsWindowVisible

out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
pids = set()
for line in out.splitlines():
    parts = [p.strip('"') for p in line.split('","')]
    if len(parts) > 1:
        try: pids.add(int(parts[1]))
        except ValueError: pass
print("python pids", sorted(pids))

results = []
CALLBACK = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
def cb(hwnd, lparam):
    pid = wt.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in pids:
        n = GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 2)
        GetWindowTextW(hwnd, buf, n + 2)
        ex = GetWindowLongW(hwnd, -20)
        rc = wt.RECT()
        GetWindowRect(hwnd, ctypes.byref(rc))
        results.append((hwnd, pid.value, bool(IsWindowVisible(hwnd)), ex,
                        rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top, buf.value))
    return True
EnumWindows(CALLBACK(cb), 0)
for r in results:
    hwnd, pid, vis, ex, x, y, w, h, title = r
    print(f"hwnd={hwnd} pid={pid} vis={vis} ex=0x{ex:X} layered={bool(ex & 0x80000)} trans={bool(ex & 0x20)} rect={x},{y} {w}x{h} title={title!r}")
