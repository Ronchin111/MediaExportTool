# -*- coding: utf-8 -*-
"""下载崩溃容错公共层：浏览器会话 + 落盘收编 + xlsx 校验 + 导出收口。

由抖音/小红书共用，消除两处几乎重复的内联实现。
视频号因是 CDP 外接 Chrome（非 launch_persistent_context），继续沿用其 dl_utils。
"""
import asyncio
import os
import shutil
import time


async def wait_for_file(dl_root, before, timeout=150, stable_rounds=4, poll=1.5, log=print):
    """轮询下载目录，等文件出现且大小稳定；浏览器崩溃时收编 .crdownload。"""
    deadline = time.time() + timeout
    last_size = {}
    stable = {}
    while time.time() < deadline:
        await asyncio.sleep(poll)
        try:
            current = set(os.listdir(dl_root))
        except FileNotFoundError:
            return None
        for f in sorted(current - before):
            path = os.path.join(dl_root, f)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if not f.endswith(".crdownload"):
                log(f"  ✓ 文件已落地: {f}（{size} bytes）")
                return path
            if size > 0 and last_size.get(f) == size:
                stable[f] = stable.get(f, 0) + 1
                if stable[f] >= stable_rounds - 1:
                    log(f"  ✓ 中途文件已写完（浏览器可能崩了）: {f}（{size} bytes）")
                    return path
            else:
                stable[f] = 0
            last_size[f] = size
    log(f"  ✗ 等待超时（{timeout}s），未发现下载文件")
    return None


def validate_xlsx(path):
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = ws.max_row or 0
        wb.close()
        return rows
    except Exception:
        return None


class Session:
    """持久化上下文浏览器会话：CDP 指定下载目录 + 崩溃自动重建。"""

    def __init__(self, pw, profile_dir, chrome_path, download_root, log=print, check_login=None):
        self.pw = pw
        self.profile_dir = profile_dir
        self.chrome_path = chrome_path
        self.download_root = download_root
        self.log = log
        self.check_login = check_login
        self.context = None
        self.page = None

    async def start(self):
        self.log("启动浏览器（专用Profile）...")
        self.context = await self.pw.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            executable_path=self.chrome_path,
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--start-minimized"],
            viewport={"width": 1920, "height": 1080},
            accept_downloads=False,
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(30000)
        try:
            cdp = await self.context.new_cdp_session(self.page)
            await cdp.send("Browser.setDownloadBehavior", {
                "behavior": "allow",
                "downloadPath": self.download_root,
                "eventsEnabled": False,
            })
        except Exception as e:
            self.log(f"⚠ 设置下载目录失败（改用默认路径）: {e}")
        return self.page

    def alive(self):
        return self.page is not None and not self.page.is_closed()

    async def stop(self):
        try:
            if self.context:
                await self.context.close()
        except Exception:
            pass
        self.context = None
        self.page = None

    async def ensure(self):
        if self.alive():
            return self.page
        if self.context or self.page:
            self.log("⚠ 浏览器已崩溃/关闭，重新启动浏览器...")
            await self.stop()
            await asyncio.sleep(3)
        await self.start()
        if self.check_login:
            await self.check_login(self.page)
        return self.page


async def export_table(session, prepare, final_name, download_root, output_dir,
                       expect_rows=1, contract_key=None, validate_contract=None, log=print):
    """prepare(page) 负责导航并返回「导出数据」按钮 locator；点击后落盘收编 → 校验 → 复制。"""
    for attempt in (1, 2):
        page = await session.ensure()
        try:
            button = await prepare(page)
        except Exception as e:
            log(f"  ✗ 页面准备失败: {e}")
            await asyncio.sleep(3)
            continue
        before = set(os.listdir(download_root))
        try:
            await button.click()
            log("  → 已点击「导出数据」，等待文件落盘...")
        except Exception as e:
            log(f"  ⚠ 点击异常（可能浏览器已崩）: {e}")
        path = await wait_for_file(download_root, before, log=log)
        if path and not session.alive():
            log("  ⚠ 浏览器在下载期间崩溃了，已用落盘文件收编")
        if path:
            dest = os.path.join(output_dir, final_name)
            try:
                shutil.copyfile(path, dest)
            except Exception as e:
                log(f"  ✗ 复制文件失败: {e}")
                continue
            rows = validate_xlsx(dest)
            if rows is None or rows < expect_rows:
                log(f"  ⚠ 文件不完整，重试（第 {attempt} 次）")
                continue
            log(f"  ✓ 校验通过: {os.path.basename(dest)}（{rows} 行）")
            log(f"  → 保存路径: {dest}")
            contract = None
            if contract_key and validate_contract:
                contract = validate_contract(dest, contract_key)
                if contract.get("ok"):
                    log(f"  ✓ 契约校验通过: {os.path.basename(dest)}")
                else:
                    log(f"  ✗ 契约校验未通过: {contract.get('reason')}")
            return dest, contract
        else:
            log(f"  ⚠ 未拿到文件，重试（第 {attempt} 次）")
        await asyncio.sleep(3)
    return None, None
