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
for _p in (HERE, os.path.dirname(HERE)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from tool_utils import find_chrome, get_chrome_port
from adaptive import validate_file_contract, write_manifest
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'

CHROME_PORT = get_chrome_port()



TASKS = [
    ("全部视频",    "run_video_all_export.py"),
    ("单篇视频",    "run_single_video_export_fresh.py"),
    ("关注者数据",  "run_follower_export.py"),
    ("来源分布",    "run_source_dist_export.py"),
    ("粉丝画像",    "run_fans_portrait_export.py"),
]

TASK_CONTRACTS = {
    "全部视频":   ("wechat_video_all_{date}.csv", "wechat_video_all"),
    "单篇视频":   ("wechat_video_single_{date}.csv", "wechat_video_single"),
    "关注者数据": ("wechat_follower_{date}.csv", "wechat_follower"),
    "来源分布":   ("wechat_source_dist_{date}.txt", "wechat_source_dist"),
    "粉丝画像":   ("wechat_fans_portrait_{date}.txt", "wechat_fans_portrait"),
}


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


def _channels_pages():
    """返回 9222/配置端口上属于视频号后台的 page（用于识别是不是我们自己的实例）"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json", timeout=3) as r:
            return [p for p in json.loads(r.read())
                    if p.get("type") == "page" and "channels.weixin.qq.com" in p.get("url", "")]
    except Exception:
        return []


def is_chrome_ready():
    """仅当端口上存在视频号后台页面时，才认为是我们自己的 Chrome 实例"""
    if not _channels_pages():
        return None
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=3) as r:
            return json.loads(r.read()).get("webSocketDebuggerUrl")
    except Exception:
        return None


def bring_front():
    """需要扫码时把视频号 Chrome 窗口带到前台（配合 --start-minimized）。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=3) as r:
            ws = json.loads(r.read()).get("webSocketDebuggerUrl")
    except Exception:
        return
    if not ws:
        return

    async def _do():
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(ws)
                pages = browser.contexts[0].pages if browser.contexts else []
                if pages:
                    await pages[0].bring_to_front()
                await browser.close()
        except Exception:
            pass

    try:
        asyncio.run(_do())
    except Exception:
        pass


def start_chrome():
    """Launch Chrome with remote debugging port via subprocess, then wait for CDP ready"""
    log("检查 Chrome 调试端口...")
    ws = is_chrome_ready()
    if ws:
        log("Chrome 调试端口已就绪 - 复用现有实例")
        return ws

    # 端口已有响应、但不是我们的视频号 Chrome：拒绝复用/关闭，避免误操作陛下浏览器
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=2) as r:
            r.read()
        log(f"[ERROR] 端口 {CHROME_PORT} 被其他程序占用，且不是本工具的视频号 Chrome，已停止")
        return None
    except Exception:
        pass

    log("Chrome 未就绪，尝试启动...")
    profile_dir = os.path.join(os.path.dirname(HERE), "chrome_profile", "WechatAutomation")
    os.makedirs(profile_dir, exist_ok=True)

    cmd = [
        CHROME_PATH,
        f"--remote-debugging-port={CHROME_PORT}",
        f"--user-data-dir={profile_dir}",
        "--disable-blink-features=AutomationControlled",
        "--start-minimized",
        "--no-first-run",
        "--no-default-browser-check",
        "--safebrowsing-disable-download-protection",
        "--disable-features=DownloadBubble,DownloadBubbleV2",
        "--disable-download-notification",
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
    bring_front()
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
    """run single export script (capture mode - reliable across pipe chains)"""
    script_path = os.path.join(SCRIPTS_DIR, script_rel)
    if not os.path.exists(script_path):
        log(f"[SKIP] script not found: {script_rel}")
        return False
    log(f"-> start: {name}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["MEDIAEXPORT_OUTPUT_DIR"] = OUTPUT_DIR
    result = subprocess.run(
        [sys.executable, "-u", script_path],
        cwd=SCRIPTS_DIR, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
    ok = result.returncode == 0
    if ok:
        log(f"  [OK] {name} done")
    else:
        log(f"  [FAIL] {name} failed (exit: {result.returncode})")
    return ok
def _close_all_pages():
    """Close all Chrome CDP pages"""
    try:
        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json", timeout=3).read())
        for t in targets:
            if t.get("type") == "page":
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/close/{t['id']}", timeout=3)
                except:
                    pass
    except:
        pass

def main():
    try:
        _main_impl()
    finally:
        log("Closing browser...")
        _close_all_pages()
        log("Browser closed")

def _main_impl():
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
    manifest_items = []
    date_tag = datetime.date.today().strftime("%Y%m%d")
    for idx, (name, script_rel) in enumerate(TASKS):
        log(f"\n{'─' * 40}")
        log_step(f"{idx+1}/{len(TASKS)}", f"执行: {name}")
        # 上一个任务可能把浏览器搞崩了 —— 崩了就先重启再跑
        if not is_chrome_ready():
            log("  ⚠ Chrome 调试端口已断开（浏览器崩溃），重新启动浏览器...")
            if not start_chrome():
                log("  ✗ 浏览器重启失败，跳过该任务")
                results[name] = False
                manifest_items.append({"task": name, "file": None, "status": "FAIL", "strategy": [], "checks": {}, "rows": None, "period": None, "error": "浏览器重启失败"})
                continue
        ok = run_task(name, script_rel)
        # 任务失败且浏览器已经没了 → 重启后重试一次（本机 Chrome 下载崩溃是已知问题）
        if not ok and not is_chrome_ready():
            log(f"  ⚠ 「{name}」失败且浏览器已崩溃，重启浏览器后重试一次...")
            if start_chrome():
                ok = run_task(f"{name}（重试）", script_rel)
        results[name] = ok
        item = {"task": name, "file": None, "status": "OK" if ok else "FAIL", "strategy": [], "checks": {}, "rows": None, "period": None, "error": None}
        if ok:
            fname, ckey = TASK_CONTRACTS[name]
            fname = fname.format(date=date_tag)
            item["file"] = fname
            fpath = os.path.join(OUTPUT_DIR, fname)
            if os.path.exists(fpath):
                contract = validate_file_contract(fpath, ckey)
                item["checks"] = {"contract": bool(contract.get("ok"))}
                if contract.get("ok"):
                    log(f"  ✓ 契约校验通过 {name}")
                else:
                    item["status"] = "FAIL"
                    item["error"] = contract.get("reason")
                    results[name] = False
                    log(f"  ✗ 契约校验未通过 {name}: {contract.get('reason')}")
            else:
                item["status"] = "FAIL"
                item["error"] = f"产物缺失: {fname}"
                results[name] = False
                log(f"  ✗ 产物缺失 {name}: {fname}")
        manifest_items.append(item)

    # 4. 汇总
    log(f"\n{'=' * 50}")
    log("导出结果汇总")
    log("=" * 50)
    ok_count = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        icon = "✓" if ok else "✗"
        log(f"  {icon} {'[OK]' if ok else '[FAIL]'} {name}")
    log(f"成功 {ok_count}/{len(results)}")
    fail_count = sum(1 for v in results.values() if not v)
    write_manifest(OUTPUT_DIR, "wechat", manifest_items, {"ok": ok_count, "fallback": 0, "fail": fail_count})
    log("  → manifest 已保存")
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
    
    os.startfile(OUTPUT_DIR)


if __name__ == "__main__":
    main()
