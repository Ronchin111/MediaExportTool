# -*- coding: utf-8 -*-
"""
MediaExport 鍙垎鍙戠増 - 宸ュ叿妯″潡
闆嗕腑绠＄悊锛氳矾寰勮В鏋愩€丆hrome 鑷姩妫€娴嬨€侀厤缃姞杞?"""
import json, os, subprocess, sys, urllib.request

_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_TOOL_DIR)

def project_root():
    return _PROJECT_ROOT

_CONFIG_CACHE = None

def load_config():
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    cfg_path = os.path.join(_PROJECT_ROOT, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8-sig") as f:
            _CONFIG_CACHE = json.load(f)
    else:
        _CONFIG_CACHE = {
            "version": "2.1",
            "chrome": {"auto_detect": True, "custom_path": "", "port": 9222, "profile_dir": "chrome_profile"},
            "output": {"dir": "output"},
            "downloads": {"dir": "downloads"},
            "backup": {"dir": "backup"},
            "platforms": {
                "douyin": {"enabled": True, "profile_name": "DouyinAutomation"},
                "xhs": {"enabled": True, "profile_name": "XiaohongshuAutomation"},
                "wechat": {"enabled": True, "profile_name": "WechatAutomation"}
            }
        }
    return _CONFIG_CACHE

def get_output_dir():
    env_dir = os.environ.get("MEDIAEXPORT_OUTPUT_DIR")
    if env_dir:
        return env_dir
    cfg = load_config()
    return os.path.join(_PROJECT_ROOT, cfg.get("output", {}).get("dir", "output"))

def get_downloads_dir():
    cfg = load_config()
    return os.path.join(_PROJECT_ROOT, cfg.get("downloads", {}).get("dir", "downloads"))

def get_backup_dir():
    cfg = load_config()
    return os.path.join(_PROJECT_ROOT, cfg.get("backup", {}).get("dir", "backup"))

def get_chrome_port():
    cfg = load_config()
    return cfg.get("chrome", {}).get("port", 9222)

def get_platform_profile_dir(platform):
    cfg = load_config()
    pname = cfg.get("platforms", {}).get(platform, {}).get("profile_name", platform.title() + "Automation")
    prel = cfg.get("chrome", {}).get("profile_dir", "chrome_profile")
    return os.path.join(_PROJECT_ROOT, prel, pname)

def find_chrome():
    cfg = load_config()
    custom = cfg.get("chrome", {}).get("custom_path", "")
    if custom and os.path.exists(custom):
        return custom
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        candidates.append(os.path.join(la, r"Google\Chrome\Application\chrome.exe"))
        candidates.append(os.path.join(la, r"Microsoft\Edge\Application\msedge.exe"))
    for p in candidates:
        if os.path.exists(p):
            return p
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for n in ("chrome.exe", "google-chrome.exe", "msedge.exe"):
            full = os.path.join(d, n)
            if os.path.exists(full):
                return full
    return None

def check_basic_env():
    results = {}
    try:
        r = subprocess.run([sys.executable, "--version"], capture_output=True, text=True, timeout=5)
        results["python"] = r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        results["python"] = None
    try:
        r = subprocess.run([sys.executable, "-c", "import playwright"], capture_output=True, timeout=5)
        results["playwright"] = r.returncode == 0
    except Exception:
        results["playwright"] = False
    cp = find_chrome()
    results["chrome"] = cp is not None
    results["chrome_path"] = cp or ""
    out_dir = get_output_dir()
    results["output_dir"] = out_dir
    results["output_ready"] = os.path.exists(out_dir) or os.access(os.path.dirname(out_dir) or ".", os.W_OK)
    for plat in ["douyin", "xhs", "wechat"]:
        results[plat + "_profile"] = os.path.exists(get_platform_profile_dir(plat))
    return results

def is_chrome_cdp_ready(port=None):
    if port is None:
        port = get_chrome_port()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return json.loads(r.read()).get("webSocketDebuggerUrl")
    except Exception:
        return None
