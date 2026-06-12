# -*- coding: utf-8 -*-
"""
视频号 - 一键全量数据导出
自动连接 Chrome CDP，顺序执行全部导出任务
"""
import asyncio, os, sys, subprocess, datetime, time, json, urllib.request
from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(HERE, "wechat")
OUTPUT_DIR = os.environ.get(
    "MEDIAEXPORT_OUTPUT_DIR",
    os.path.join(HERE, "..", "output")
)
BACKUP_DIR = os.path.join(HERE, "..", "backup")
from tool_utils import find_chrome
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'

CHROME_PORT = 9222

TASKS = [
    ("全部视频",    "run_video_all_export.py"),
    ("单篇视频",    "run_single_video_export_fresh.py"),
    ("关注者数据",  "run_follower_export.py"),
    ("来源分布",    "run_source_dist_export.py"),
    ("粉丝画像",    "run_fans_portrait_export.py"),
]


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        print(f"[{ts}] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[{ts}] {msg.encode('utf-8', errors='replace').decode('utf-8', errors='replace')}", flush=True)


def log_step(step, msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    try:
        print(f"[{ts}] [{step}] {msg}", flush=True)
    except UnicodeEncodeError:
        safe = msg.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
        print(f"[{ts}] [{step}] {safe}", flush=True)


def is_chrome_ready():
    """检查 Chrome 调试端口是否就绪"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=3) as r:
            return json.loads(r.read()).get("webSocketDebuggerUrl")
    except Exception:
        return None


def start_chrome():
    """Launch Chrome with remote debugging port via subprocess, then wait for CDP ready"""
    log("检查 Chrome 调试端口...")
    ws = is_chrome_ready()
    if ws:
        log("Chrome 调试端口已就绪 - 复用现有实例")
        return ws

    log("Chrome 未就绪，尝试启动...")
    profile_dir = os.path.join(os.path.dirname(HERE), "chrome_profile", "WechatAutomation")
    os.makedirs(profile_dir, exist_ok=True)

    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={CHROME_PORT}",
        f"--user-data-dir={profile_dir}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "https://channels.weixin.qq.com/micro/statistic",
    ]
    try:
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        log("Chrome 已启动（调试端口 9222）")
    except Exception as exc:
        log(f"[ERROR] Chrome 启动失败: {exc}")
        return None

    # 等待 CDP 端口就绪
    log("等待 Chrome CDP 端口就绪...")
    for i in range(30):
        time.sleep(1)
        ws = is_chrome_ready()
        if ws:
            log("CDP 端口已就绪")
            break
        if i % 5 == 4:
            log(f"  等待 CDP... ({i+1}/30秒)")
    else:
        log("[ERROR] CDP 端口启动超时（30秒）")
        return None

    # 等待登录完成
    log("【需要扫码】视频号登录可能已过期，请在弹出的浏览器窗口中扫码")
    log("等待登录检测...")
    for i in range(180):
        time.sleep(1)
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json", timeout=2) as r2:
                pages = json.loads(r2.read())
            in_platform = any("channels.weixin.qq.com/platform" in p2.get("url", "") for p2 in pages)
            if in_platform:
                log("登录检测成功")
                ws2 = is_chrome_ready()
                if ws2:
                    return ws2
        except Exception:
            pass
        if i % 15 == 0 and i > 0:
            log(f"  等待登录... ({i}s)")

    log("[ERROR] 登录超时（3分钟）")
    return None
    return None
def backup_existing():
    """备份现有导出文件"""
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        return
    ts = datetime.datetime.now().strftime("%m%d_%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    count = 0
    for fn in os.listdir(OUTPUT_DIR):
        src = os.path.join(OUTPUT_DIR, fn)
        if os.path.isfile(src):
            dst = os.path.join(BACKUP_DIR, f"{ts}_{fn}")
            try:
                import shutil
                shutil.copy2(src, dst)
                count += 1
            except Exception:
                pass
    if count:
        log(f"✓ 已备份 {count} 个现有文件到 backup/")
    else:
        log("无需备份（输出目录为空）")


def run_task(name, script_rel):
    """运行单个导出脚本"""
    script_path = os.path.join(SCRIPTS_DIR, script_rel)
    if not os.path.exists(script_path):
        log(f"[SKIP] 脚本不存在: {script_rel}")
        return False
    log(f"→ 开始: {name}")
    log(f"  脚本: {script_rel}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["MEDIAEXPORT_OUTPUT_DIR"] = OUTPUT_DIR
    result = subprocess.run(
        [sys.executable, "-u", script_path],
        cwd=SCRIPTS_DIR, env=env,
        capture_output=False
    )
    ok = result.returncode == 0
    if ok:
        log(f"✓ [OK] {name} 完成")
    else:
        log(f"✗ [FAIL] {name} 失败 (exit code: {result.returncode})")
    return ok


def main():
    log("=" * 50)
    log("视频号数据一键导出")
    log("=" * 50)
    log(f"输出目录: {OUTPUT_DIR}")
    log(f"备份目录: {BACKUP_DIR}")
    log(f"共 {len(TASKS)} 个导出任务：")
    for idx, (name, _) in enumerate(TASKS):
        log(f"  [{idx+1}/{len(TASKS)}] {name}")

    # 1. 确保 Chrome 就绪
    log_step("准备", "检查 Chrome CDP 连接...")
    ws = is_chrome_ready()
    if not ws:
        log_step("准备", "Chrome 未就绪，尝试启动...")
        ws = start_chrome()
        if not ws:
            log("[ERROR] Chrome 启动失败，请手动检查")
            sys.exit(1)
    else:
        log_step("准备", "✓ Chrome CDP 已连接")

    # 2. 备份现有输出
    log_step("备份", "备份现有导出文件...")
    backup_existing()

    # 3. 执行所有任务
    results = {}
    for idx, (name, script_rel) in enumerate(TASKS):
        log(f"\n{'─' * 40}")
        log_step(f"{idx+1}/{len(TASKS)}", f"执行: {name}")
        ok = run_task(name, script_rel)
        results[name] = ok

    # 4. 汇总
    log(f"\n{'=' * 50}")
    log("导出结果汇总")
    log("=" * 50)
    ok_count = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        icon = "✓" if ok else "✗"
        log(f"  {icon} {'[OK]' if ok else '[FAIL]'} {name}")
    log(f"成功 {ok_count}/{len(results)}")
    log(f"输出目录: {OUTPUT_DIR}")

    if ok_count == len(results):
        log("🎉 视频号全部数据导出成功！")
    elif ok_count > 0:
        log(f"⚠ 视频号部分导出成功（{len(results) - ok_count}项失败）")
    else:
        log("✗ 视频号全部导出失败")

    log("=" * 50)

    if ok_count < len(results):
        sys.exit(1)

    # 打开输出目录
    os.startfile(OUTPUT_DIR)


if __name__ == "__main__":
    main()
