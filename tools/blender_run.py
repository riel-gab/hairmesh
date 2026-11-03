"""Every Blender this project starts goes through here (the owner's rule of 22/10/2026: they use the main monitor
while the work runs, so nothing may open in front of them or take the focus).

- With a window (captures, event simulations, the benchmark): on the secondary monitor, `-p` + `--no-window-focus`.
  Blender's `-p <sx> <sy> <w> <h>` is the CLIENT area with the origin at the BOTTOM-left of the primary screen
  (measured on 22/10/2026: `-p 2000 100 1280 720` gave the window rect 1992,229 - 3288,988 on a 1080-high primary:
  8 px of invisible border on each side, ~31 px of title bar above the client area). With no secondary monitor the
  window opens minimized without activation and the run goes on.
- In background (`-b`): no console window (CREATE_NO_WINDOW), the output captured.

- With --factory-startup: a user folder of its own, empty (BLENDER_USER_RESOURCES). A factory-settings Blender that
  installs or enables an extension syncs the wheels to that one extension and deletes those of every other, in the
  profile it runs on (measured: tools/wheel_factory_check.py, docs/testes.md); here it has no profile to harm.
  HAIRMESH_REAL_PROFILE=1 turns this off.

As a library: `run(args, timeout=...)` -> subprocess.CompletedProcess; `window_args()`; `blender_exe()`.
From the command line (prints the output, exits with Blender's code):

    python tools/blender_run.py -- -b --factory-startup --python-exit-code 1 --python tests/run_tests.py
    python tools/blender_run.py -- --factory-startup --enable-event-simulate --python tools/sim_c37_shots.py -- <repo> <out>
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_BLENDER = r'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe'
CREATE_NO_WINDOW = 0x08000000
SW_SHOWMINNOACTIVE = 7
BORDER = 8                                                          # the invisible resize border Windows puts around a window
TITLE = 32                                                          # the title bar above the client area (31 measured) + 1


def blender_exe():
    exe = os.environ.get('HAIRMESH_BLENDER')
    if exe:
        return exe
    try:
        import bpy
        return bpy.app.binary_path
    except ImportError:
        pass
    return DEFAULT_BLENDER if os.path.exists(DEFAULT_BLENDER) else shutil.which('blender')


def monitors():
    """[(x, y, w, h, primary)] of the monitors, in Windows' virtual-screen coordinates (y down); [] off Windows"""
    if sys.platform != 'win32':
        return []
    from ctypes import wintypes
    out = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        out.append((r.left, r.top, r.right - r.left, r.bottom - r.top, bool(mi.dwFlags & 1)))
        return 1
    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    except Exception:
        return []
    return out


def secondary():
    """the first monitor that is not the primary, or None"""
    return next((m for m in monitors() if not m[4]), None)


def window_args():
    """`-p sx sy w h --no-window-focus` filling the secondary monitor, or None when there is none"""
    mons = monitors()
    sec = next((m for m in mons if not m[4]), None)
    prim = next((m for m in mons if m[4]), None)
    if sec is None or prim is None:
        return None
    x, y, w, h, _ = sec
    cw, ch = w - 2 * BORDER, h - TITLE - 2 * BORDER
    sx = x + BORDER
    sy = (prim[1] + prim[3]) - (y + h) + BORDER                     # Blender's y: up from the bottom of the primary screen
    return ['-p', str(sx), str(sy), str(cw), str(ch), '--no-window-focus']


def isolated_profile():
    """the empty user folder of every --factory-startup Blender (shared: such a Blender writes nothing there)"""
    path = os.path.join(tempfile.gettempdir(), 'hairmesh-factory-profile')
    os.makedirs(path, exist_ok=True)
    return path


def command(args, exe=None):
    """the full command line and the Popen keywords for `args` (Blender's arguments)"""
    exe = exe or blender_exe()
    args = list(args)
    kw = {}
    own = args[:args.index('--')] if '--' in args else args       # (Blender's own arguments: a script's come after `--`)
    background = any(a in ('-b', '--background', '-c', '--command') for a in own)   # (--command: no window)
    if '--factory-startup' in own and not os.environ.get('HAIRMESH_REAL_PROFILE') and 'BLENDER_USER_RESOURCES' not in os.environ:
        kw['env'] = dict(os.environ, BLENDER_USER_RESOURCES=isolated_profile())
    if sys.platform == 'win32':
        if background:
            kw['creationflags'] = CREATE_NO_WINDOW
        else:
            wa = window_args()
            if wa is not None and '-p' not in args and '--window-geometry' not in args:
                args = wa + args
            elif wa is None:                                        # no secondary monitor: minimized, not activated
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = SW_SHOWMINNOACTIVE
                kw['startupinfo'] = si
                if '--no-window-focus' not in args:
                    args = ['--no-window-focus'] + args
            kw['creationflags'] = CREATE_NO_WINDOW                  # (the console Blender would open beside its window)
    return [exe] + args, kw


def run(args, timeout=None, cwd=None, exe=None):
    cmd, kw = command(args, exe)
    # (stdin closed: `--command extension build` waits on an inherited stdin pipe)
    return subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, **kw)


def run_to_file(args, path, timeout=None, cwd=None, exe=None, env=None):
    """like `run`, the output in a file; env: variables on top of this process's"""
    cmd, kw = command(args, exe)
    if env:
        kw['env'] = dict(kw.get('env', os.environ), **env)
    with open(path, 'w', encoding='utf-8') as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, **kw)
        return p.wait(timeout=timeout)


def main(argv):
    if '--' not in argv:
        print(__doc__)
        return 2
    args = argv[argv.index('--') + 1:]
    r = run(args)
    for stream in (sys.stdout, sys.stderr):                         # (a Windows console is cp1252: Blender's output carries arrows, ×, —)
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
