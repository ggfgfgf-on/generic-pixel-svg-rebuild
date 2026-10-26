import sys, os, json, subprocess, glob, shutil

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

def find_browser():
    env = os.environ.get("BROWSER_BIN")
    if env and os.path.exists(env):
        return env
    for p in EDGE_CANDIDATES:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "chromium"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None

def render(html, out, w, h, suffix=""):
    exe = find_browser()
    if not exe:
        raise SystemExit("no Edge/Chrome found; set BROWSER_BIN")
    url = "file:///" + os.path.abspath(html).replace("\\", "/") + suffix
    out_abs = os.path.abspath(out)
    if os.path.exists(out_abs):
        os.remove(out_abs)
    cmd = [exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check",
           "--window-size=%d,%d" % (w, h), "--screenshot=" + out_abs,
           "--virtual-time-budget=3000", url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_abs):
        raise SystemExit("screenshot failed: " + (r.stderr or r.stdout)[-400:])
    return out_abs

if __name__ == "__main__":
    html, out = sys.argv[1], sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 800
    suffix = sys.argv[5] if len(sys.argv) > 5 else ""
    p = render(html, out, w, h, suffix)
    print(json.dumps({"out": p, "w": w, "h": h, "browser": find_browser()}))
