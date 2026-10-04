"""抖音创作者中心 - 一键数据导出 v3

适配 2026-10 新版创作者中心页面结构（2026-10-04 实机探针验证）。

一、页面结构变化（这是「导不出来」的主因）
  * 数据中心不再是「账号总览 / 作品分析 / 粉丝画像」多个子菜单，而是单页
    https://creator.douyin.com/creator-micro/data-center/operation
    页面内三个版块：数据总览 / 作品数据 / 粉丝数据，
    每块各自带「昨天 / 近7天 / 近30天」时间范围和「导出数据」按钮。
  * 侧边栏只剩：首页 / 内容管理 / 数据中心 / 收入变现 / 创作服务，
    旧文字入口「账号总览」「作品分析」「投稿列表」已不存在。
  * 作品列表改到 https://creator.douyin.com/creator-micro/content/manage
    的「作品」列表页，筛选栏右上角有「导出数据」按钮。

二、下载容错（Chrome 偶发崩溃）
  实测本机 Chrome 154 在下载完成瞬间约半数概率进程崩溃
  （退出码 0xC0000005，浏览器整个消失，但文件数据已写完，
  只会留在下载目录里作为 *.crdownload）。
  因此这里不再依赖 Playwright 的下载事件，而是：
    1) 用 CDP Browser.setDownloadBehavior 指定固定下载目录；
    2) 点击导出后轮询目录，等文件出现且大小稳定；
    3) 若浏览器中途崩了，直接把已写完的 .crdownload 收编为正式文件；
    4) xlsx 用 openpyxl 校验能打开才算成功，失败自动重试一次；
    5) 浏览器崩了不影响后续任务 —— 下一个任务会自动重开浏览器。

4 项输出（保持旧文件名，兼容下游）
  1. 播放量数据    作品数据版块切「近30天」→ 导出 xlsx   → douyin_play_data_YYYYMMDD.xlsx
  2. 作品列表      内容管理页「作品」列表 → 导出 xlsx     → douyin_video_list_YYYYMMDD.xlsx
  3. 账号总览指标  抓取三个版块近7天指标                  → douyin_account_overview_7days_*.txt
  4. 粉丝画像      抓取 fans/summary/v2 接口              → douyin_fan_portrait_*.txt
"""
import asyncio
import json
import os
import shutil
import sys
import time
import traceback
from datetime import datetime
from playwright.async_api import async_playwright

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_SCRIPT_DIR, os.path.dirname(_SCRIPT_DIR)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from tool_utils import get_platform_profile_dir, find_chrome
from adaptive import (
    load_json, resolve_first, is_fallback, check_fingerprint,
    read_fingerprint, write_fingerprint, validate_xlsx_contract, write_manifest, save_diag,
)
from resilience import Session, wait_for_file, validate_xlsx, export_table

DEDICATED_USER_DATA = get_platform_profile_dir('douyin')
CHROME_PATH = find_chrome() or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
OUTPUT_DIR = os.environ.get(
    "MEDIAEXPORT_OUTPUT_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output"),
)
TOOL_ROOT = os.path.dirname(_SCRIPT_DIR)
DL_ROOT = ""                  # 运行时下载临时目录（main 里创建）

# ── 自适应容错配置 ──
SELECTORS = load_json(os.path.join(_SCRIPT_DIR, "douyin_selectors.json"), default={})
FINGERPRINT_PATH = os.path.join(_SCRIPT_DIR, "douyin_fingerprint.json")
CTX = {
    "fallback": False,            # 是否命中过非首选策略
    "current_task": "",           # 当前任务名（用于把策略归到任务）
    "strategies": [],             # [(task, target, strategy, is_fallback)]
    "fingerprint_found": [],
    "fingerprint_missing": [],
    "items": [],
}

# ── 页面地址（2026-10 新版）──
HOME_URL = "https://creator.douyin.com/creator-micro/home"
DATA_CENTER_URL = "https://creator.douyin.com/creator-micro/data-center/operation"
CONTENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/content/manage"

FANS_SUMMARY_API = "/janus/douyin/creator/bff/data/fans/summary/v2"

DOWNLOAD_TIMEOUT = 150        # 单次导出等待落盘上限（秒）
STABLE_ROUNDS = 4             # 文件大小连续不变次数（4 × 1.5s = 6s）→ 认定写完
POLL_INTERVAL = 1.5
PAGE_TIMEOUT = 30000

SECTION_OVERVIEW = "数据总览"
SECTION_VIDEO = "作品数据"
SECTION_FANS = "粉丝数据"

OVERVIEW_LABELS = ["播放量", "互动率", "完播率", "作品数", "粉丝净增"]
VIDEO_LABELS = [
    "投稿量", "总播放量", "总点赞量", "总分享量", "总评论量",
    "5秒完播率", "2秒跳出率", "封面点击率", "平均播放时长",
]
FANS_LABELS = ["总粉丝量", "粉丝净增", "吸粉量", "脱粉量", "回访粉丝量"]


# ────────────────────────── 日志 ──────────────────────────

def ts_now():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def log_info(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def log_step(step, msg):
    print(f"[{datetime.now():%H:%M:%S}] [{step}] {msg}", flush=True)


def banner(text):
    print("\n" + "=" * 50)
    print(text)
    print("=" * 50)


def record_strategy(target, spec, strategy):
    """记录一次定位用的策略；非首选策略会标记兜底并写进全局状态。"""
    fb = is_fallback(spec, strategy)
    if fb:
        CTX["fallback"] = True
    CTX["strategies"].append((CTX["current_task"], target, strategy, fb))
    if fb:
        log_info(f"  ⚠ 兜底策略命中（{target}）: {strategy}")


def task_strategies(task):
    return [s[2] for s in CTX["strategies"] if s[0] == task]


def task_fallback(task):
    return any(s[3] for s in CTX["strategies"] if s[0] == task)


def finalize_item(task, file=None, contract=None, rows=None, period=None, error=None):
    """按四要素 + 契约校验结果，把一次任务收口成 manifest 条目。"""
    if error is None and contract is not None and not contract.get("ok"):
        error = contract.get("reason")
    checks = {}
    if contract is not None:
        checks = {
            "openable": True,
            "required_columns": bool(contract.get("ok")),
            "min_rows": bool(contract.get("ok")),
        }
    elif file:
        checks = {"written": True}

    if file is None:
        status = "FAIL"
    elif contract is not None and not contract.get("ok"):
        status = "FAIL"
    elif task_fallback(task):
        status = "OK(兜底)"
    else:
        status = "OK"

    return {
        "task": task,
        "file": os.path.basename(file) if file else None,
        "status": status,
        "strategy": task_strategies(task),
        "checks": checks,
        "rows": rows,
        "period": period,
        "error": error,
    }


async def check_login(page):
    """访问创作者中心首页，必要时等待扫码"""
    log_info("正在访问抖音创作者中心...")
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    await asyncio.sleep(4)
    if "login" in page.url.lower() or "passport" in page.url.lower():
        try:
            await page.bring_to_front()
        except Exception:
            pass
        print("\n" + "=" * 60)
        print("  【需要扫码】抖音创作者中心登录已过期")
        print("  请在弹出的浏览器窗口中扫码重新登录")
        print("  等待自动检测登录...")
        print("=" * 60)
        for i in range(180):
            await asyncio.sleep(1)
            current = page.url.lower()
            if "login" not in current and "passport" not in current:
                log_info("  ✓ 登录检测成功，继续执行...")
                break
            if i % 15 == 0 and i > 0:
                log_info(f"  等待登录... ({i}秒)")
        else:
            log_info("✗ 登录超时（3分钟）")
            return False
        await asyncio.sleep(3)
    log_info("✓ 已登录")
    return True


async def safe_screenshot(page, name):
    try:
        path = os.path.join(OUTPUT_DIR, f"{name}_{ts_now()}.png")
        await page.screenshot(path=path, full_page=True)
        log_info(f"  → 截图已保存: {os.path.basename(path)}")
    except Exception as e:
        log_info(f"  → 截图失败: {str(e)[:80]}")


async def save_drift_diag(page, missing):
    try:
        shot = os.path.join(OUTPUT_DIR, f"_diag_shot_{ts_now()}.png")
        await page.screenshot(path=shot)
        text = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
        save_diag(OUTPUT_DIR, "douyin", "drift", screenshot_path=shot, page_text=text,
                  strategies=[s[2] for s in CTX["strategies"]], error=f"缺关键文本: {missing}")
        log_info("  → 诊断包已保存到 output/diag/")
    except Exception as e:
        log_info(f"  → 诊断包保存失败: {str(e)[:80]}")


class DouyinSession(Session):
    """在公共 Session 基础上，每次启动/重建时挂粉丝画像接口监听。"""

    def __init__(self, pw, profile_dir, chrome_path, download_root, captured):
        super().__init__(pw, profile_dir, chrome_path, download_root, log=log_info, check_login=check_login)
        self.captured = captured

    async def start(self):
        page = await super().start()

        async def on_response(response):
            if FANS_SUMMARY_API in response.url:
                try:
                    self.captured["bodies"].append(await response.text())
                except Exception:
                    pass

        page.on("response", on_response)
        return page


# ────────────────────── 页面定位辅助 ──────────────────────

async def section_header(page, title):
    """按选择器配置找版块容器并记录策略；找不到返回 None。"""
    spec = SELECTORS.get("section", {})
    loc, strat = await resolve_first(page, spec, params={"title": title})
    record_strategy(f"版块[{title}]", spec, strat)
    if loc is None:
        log_info(f"  ✗ 未找到「{title}」版块容器")
    return loc


async def goto_data_center(page):
    try:
        await page.goto(DATA_CENTER_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log_info(f"  ✗ 打开数据中心失败: {str(e)[:120]}")
        return False
    await asyncio.sleep(6)
    try:
        await page.get_by_text(SECTION_VIDEO, exact=True).first.wait_for(state="visible", timeout=25000)
    except Exception:
        log_info("  ✗ 数据中心版块未渲染出来")
        return False

    # L3 页面指纹：比对关键结构文本，缺了即报漂移
    required = SELECTORS.get("fingerprint", {}).get("required", [])
    if not required:
        log_info("  ⚠ L3 指纹配置缺失，漂移检测本次未生效")
    else:
        found, missing = await check_fingerprint(page, required)
        CTX["fingerprint_found"] = found
        CTX["fingerprint_missing"] = missing
        baseline = read_fingerprint(FINGERPRINT_PATH)
        if missing:
            log_info(f"  ⚠ 疑似页面改版，缺关键文本: {missing}")
            await save_drift_diag(page, missing)
        elif baseline and set(found) != set(baseline):
            log_info(f"  ⚠ 页面指纹与上次基线不一致（本次 {len(found)} 项 / 基线 {len(baseline)} 项）")
    return True


async def switch_range(page, header, label, name):
    if header is None:
        log_info(f"  ⚠ {name}: 版块容器不存在，无法切换「{label}」")
        return False
    spec = SELECTORS.get("range_radio", {})
    radio, strat = await resolve_first(page, spec, scope=header, params={"label": label})
    record_strategy(f"{name}时间范围", spec, strat)
    try:
        if radio is None:
            log_info(f"  ⚠ {name}: 未找到「{label}」选项")
            return False
        await radio.click()
        log_info(f"  ✓ {name}: 已切换到「{label}」（策略 {strat}）")
        await asyncio.sleep(4)
        return True
    except Exception as e:
        log_info(f"  ⚠ {name}: 切换「{label}」失败 - {str(e)[:120]}")
        return False


SECTION_METRICS_JS = r"""
(arg) => {
    const title = arg[0], labels = arg[1];
    const leaf = Array.from(document.querySelectorAll('*')).find(
        el => el.children.length === 0 && (el.innerText || '').trim() === title);
    if (!leaf) return null;
    let header = leaf;
    for (let i = 0; i < 6 && header.parentElement; i++) {
        header = header.parentElement;
        if ((header.className || '').toString().startsWith('header-')) break;
    }
    let card = header;
    for (let i = 0; i < 3 && card.parentElement; i++) card = card.parentElement;
    const lines = (card.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    const out = { '_period': null, '_title': title };
    for (const ln of lines) {
        if (ln.indexOf('统计周期') >= 0) { out['_period'] = ln; break; }
    }
    // 值的样子：数字/千分位/百分比/时长（如 7,664 / 0.4% / 9.74s / -2 / 1.2万）
    const isValue = (t) => /^[-+]?[\d,.]+(万|s|%)?$/.test(t.replace(/\s+/g, ''));
    for (const lab of labels) {
        const idx = lines.indexOf(lab);
        if (idx >= 0 && idx + 1 < lines.length) {
            out[lab] = lines[idx + 1];          // 标签独占一行，值在下一行
            continue;
        }
        let same = null;                        // 标签和值在同一行： 「播放量 7,664」
        for (const ln of lines) {
            if (ln === lab || !ln.startsWith(lab)) continue;
            const rest = ln.slice(lab.length).trim();
            if (isValue(rest)) { same = rest; break; }
        }
        out[lab] = same;
    }
    return out;
}
"""


async def scrape_section(page, title, labels):
    try:
        return await page.evaluate(SECTION_METRICS_JS, [title, labels])
    except Exception as e:
        log_info(f"  ✗ 抓取「{title}」异常: {str(e)[:120]}")
        return None


# ── 任务 1：播放量数据（作品数据 · 近30天 · xlsx）──

async def task1_export_play_data(session):
    banner("[1/4] 表格导出：播放量数据（作品数据 · 近30天）")
    first = True
    period_holder = {}

    async def prepare(page):
        nonlocal first
        if first:
            log_step("1/4", "打开数据中心 → 作品数据版块...")
        else:
            log_step("1/4", "重试：重新打开数据中心 → 作品数据版块...")
        first = False
        if not await goto_data_center(page):
            await safe_screenshot(page, "err_task1_page")
            raise RuntimeError("数据中心没打开")
        log_step("1/4", "切换时间范围为「近30天」...")
        header = await section_header(page, SECTION_VIDEO)
        if header is None:
            raise RuntimeError("作品数据版块不存在")
        if not await switch_range(page, header, "近30天", "作品数据"):
            raise RuntimeError("「近30天」切换失败")
        data = await scrape_section(page, SECTION_VIDEO, [])
        period_holder["period"] = (data or {}).get("_period") or "近30天"
        spec = SELECTORS.get("export_button", {})
        btn, bstrat = await resolve_first(page, spec, scope=header)
        record_strategy("导出按钮", spec, bstrat)
        if btn is None:
            raise RuntimeError("未找到「导出数据」按钮")
        return btn

    log_step("1/4", "导出「作品数据」表格...")
    dest, contract = await export_table(
        session, prepare, f"douyin_play_data_{datetime.now():%Y%m%d}.xlsx",
        DL_ROOT, OUTPUT_DIR, expect_rows=2, contract_key="douyin_play_data",
        validate_contract=validate_xlsx_contract, log=log_info,
    )
    return finalize_item(
        "播放量数据", dest, contract=contract,
        rows=(contract or {}).get("rows"), period=period_holder.get("period"),
    )


# ── 任务 2：作品列表（内容管理 · xlsx）──

async def task2_export_video_list(session):
    banner("[2/4] 表格导出：作品列表（内容管理 · 作品）")

    async def prepare(page):
        log_step("2/4", "打开内容管理 → 作品列表...")
        await page.goto(CONTENT_MANAGE_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.get_by_text("导出数据", exact=True).first.wait_for(state="visible", timeout=25000)
        except Exception:
            log_info("  ✗ 作品列表页未出现「导出数据」按钮")
            await safe_screenshot(page, "err_task2_button")
            raise RuntimeError("没有导出按钮")
        spec = SELECTORS.get("export_button", {})
        button, bstrat = await resolve_first(page, spec)
        record_strategy("导出按钮", spec, bstrat)
        if button is None:
            raise RuntimeError("没有导出按钮")
        return button

    log_step("2/4", "导出「作品列表」...")
    dest, contract = await export_table(
        session, prepare, f"douyin_video_list_{datetime.now():%Y%m%d}.xlsx",
        DL_ROOT, OUTPUT_DIR, expect_rows=2, contract_key="douyin_video_list",
        validate_contract=validate_xlsx_contract, log=log_info,
    )
    return finalize_item(
        "作品列表", dest, contract=contract,
        rows=(contract or {}).get("rows"), period="全量作品",
    )


# ── 任务 3：账号总览指标（三版块 · 近7天 · txt）──

async def task3_scrape_overview(session):
    banner("[3/4] 页面抓取：账号总览近7天指标")
    page = await session.ensure()
    log_step("3/4", "打开数据中心...")
    if not await goto_data_center(page):
        await safe_screenshot(page, "err_task3_page")
        return finalize_item("账号总览指标", error="数据中心未打开")

    log_step("3/4", "切换「作品数据」「粉丝数据」为近7天...")
    vh = await section_header(page, SECTION_VIDEO)
    fh = await section_header(page, SECTION_FANS)
    ok_v = await switch_range(page, vh, "近7天", "作品数据")
    ok_f = await switch_range(page, fh, "近7天", "粉丝数据")
    switch_failed = (not ok_v) or (not ok_f)
    await asyncio.sleep(4)

    log_step("3/4", "提取三个版块指标...")
    sections = [
        (SECTION_OVERVIEW, "数据总览（近7天）", OVERVIEW_LABELS),
        (SECTION_VIDEO, "作品数据（近7天）", VIDEO_LABELS),
        (SECTION_FANS, "粉丝数据（近7天）", FANS_LABELS),
    ]
    collected = []
    period = ""
    for title, heading, labels in sections:
        data = await scrape_section(page, title, labels)
        if not data:
            log_info(f"  ⚠ {title}: 未抓取到")
            collected.append((heading, {}))
            continue
        if not period and data.get("_period"):
            period = data["_period"]
        values = {k: v for k, v in data.items() if not k.startswith("_")}
        hit = sum(1 for v in values.values() if v)
        log_info(f"  ✓ {title}: {hit}/{len(values)} 项")
        for k, v in values.items():
            log_info(f"      {k}: {v if v else '未提取到'}")
        collected.append((heading, values))

    lines = ["抖音账号总览（近7天）", ""]
    if period:
        lines.append(period)
    lines.append(f"采集时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    for heading, values in collected:
        lines.append("")
        lines.append(f"[{heading}]")
        for k, v in values.items():
            lines.append(f"{k}: {v if v else 'N/A'}")

    txt_path = os.path.join(OUTPUT_DIR, f"douyin_account_overview_7days_{ts_now()}.txt")
    with open(txt_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))
    log_info(f"  ✓ 指标已保存: {os.path.basename(txt_path)}")
    log_info(f"  → 保存路径: {txt_path}")
    item = finalize_item("账号总览指标", txt_path, period=period or "近7天")
    if switch_failed:
        item["status"] = "FAIL"
        item["error"] = "时间范围切换失败，指标可能非近7天"
    return item


# ── 任务 4：粉丝画像（接口抓取）──

def build_fans_portrait(api_data):
    def section(title, items, unit="%"):
        if not items:
            return [f"\n{title}", "  无数据"]
        out = [f"\n{title}"]
        for item in items:
            name = (item.get("label") or item.get("name") or item.get("key")
                    or item.get("dim") or item.get("title") or "?")
            value = item.get("count")
            if value is None:
                value = item.get("value", "?")
            out.append(f"  {name}: {value}{unit}")
        return out

    lines = ["抖音粉丝画像", ""]
    lines.append(f"采集时间: {datetime.now():%Y-%m-%d %H:%M:%S}")

    total = api_data.get("fans_cnt_sum")
    d7 = api_data.get("fans_data_7") or {}
    d1 = api_data.get("fans_data_1") or {}
    lines.append("\n[概览]")
    lines.append(f"  总粉丝量: {total if total is not None else 'N/A'}")
    if d7:
        lines.append(f"  近7天净增: {d7.get('net_fans', 'N/A')}")
        lines.append(f"  近7天流失: {d7.get('cancel_fans', 'N/A')}")
        lines.append(f"  近7天主页访问粉丝: {d7.get('home_view_fans', 'N/A')}")
    if d1:
        lines.append(f"  昨日净增: {d1.get('net_fans', 'N/A')}")

    lines += section("性别分布（占比）", api_data.get("gender_distribution"))
    lines += section("年龄分布（占比）", api_data.get("age_distribution"))
    lines += section("活跃度分布（占比）", api_data.get("active_levels"))
    lines += section("地域分布（占比）",
                     api_data.get("city_distribution") or api_data.get("province_distribution"))
    lines += section("设备分布（占比）",
                     api_data.get("device_brand_distribution") or api_data.get("device_distribution"))
    lines += section("粉丝兴趣分布（占比）", api_data.get("fans_interest_distribution"))
    return "\n".join(lines)


async def task4_scrape_fans(session):
    banner("[4/4] API抓取：粉丝画像")
    before = len(session.captured["bodies"])
    page = await session.ensure()
    log_step("4/4", "打开数据中心，触发粉丝画像接口...")
    if not await goto_data_center(page):
        await safe_screenshot(page, "err_task4_page")
    else:
        try:
            await page.mouse.wheel(0, 3000)
            await asyncio.sleep(3)
        except Exception:
            pass

    for _ in range(25):
        if len(session.captured["bodies"]) > before:
            break
        await asyncio.sleep(1)

    if not session.captured["bodies"]:
        log_info("  ⚠ 未捕获到粉丝画像接口响应，截图保存")
        await safe_screenshot(page, "err_task4_api")
        return finalize_item("粉丝画像", error="未捕获到接口响应")

    body = session.captured["bodies"][-1]
    log_info(f"  ✓ 接口响应已捕获: {len(body)} bytes")
    raw_path = os.path.join(OUTPUT_DIR, f"douyin_fans_raw_{ts_now()}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(body)
    log_info(f"  → 原始JSON已保存: {os.path.basename(raw_path)}")

    log_step("4/4", "解析粉丝画像数据...")
    try:
        api_data = json.loads(body)
    except Exception as e:
        log_info(f"  ✗ JSON解析失败: {e}")
        return finalize_item("粉丝画像", error=f"JSON解析失败: {e}")

    content = build_fans_portrait(api_data)
    txt_path = os.path.join(OUTPUT_DIR, f"douyin_fan_portrait_{ts_now()}.txt")
    with open(txt_path, "w", encoding="utf-8-sig") as f:
        f.write(content)

    log_info(f"  ✓ 粉丝画像已保存: {os.path.basename(txt_path)}")
    log_info(f"  → 保存路径: {txt_path}")
    shown = content.split("\n")
    for line in shown[:18]:
        log_info(f"    {line}")
    if len(shown) > 18:
        log_info(f"    ... (共{len(shown)}行)")
    return finalize_item("粉丝画像", txt_path)


# ────────────────────────── 主流程 ──────────────────────────

async def main():
    global DL_ROOT
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    DL_ROOT = os.path.join(TOOL_ROOT, "downloads", f"douyin_{ts_now()}")
    os.makedirs(DL_ROOT, exist_ok=True)

    results = []
    session = None
    captured = {"bodies": []}
    async with async_playwright() as p:
        session = DouyinSession(p, DEDICATED_USER_DATA, CHROME_PATH, DL_ROOT, captured)
        print("=" * 60)
        print("抖音创作者中心 - 一键数据导出 v3")
        print("=" * 60)
        log_info(f"输出目录: {OUTPUT_DIR}")
        log_info(f"Chrome Profile: {DEDICATED_USER_DATA}")
        print("\n[登录检查]")
        try:
            await session.start()
            ok = await check_login(session.page)
        except Exception as e:
            log_info(f"✗ 浏览器启动失败: {e}")
            write_manifest(OUTPUT_DIR, "douyin", [], {"ok": 0, "fallback": 0, "fail": 1}, error="浏览器启动失败")
            sys.exit(1)
        if not ok:
            log_info("✗ 未登录，退出")
            await session.stop()
            write_manifest(OUTPUT_DIR, "douyin", [], {"ok": 0, "fallback": 0, "fail": 1}, error="登录超时/未登录")
            sys.exit(1)
        log_info("开始导出：播放量数据 → 作品列表 → 账号总览指标 → 粉丝画像")
        await asyncio.sleep(2)

        tasks = [
            ("播放量数据", task1_export_play_data),
            ("作品列表", task2_export_video_list),
            ("账号总览指标", task3_scrape_overview),
            ("粉丝画像", task4_scrape_fans),
        ]

        for name, fn in tasks:
            CTX["current_task"] = name
            try:
                item = await fn(session)
                results.append((name, item))
            except Exception as e:
                log_info(f"\n✗ {name} 任务异常: {type(e).__name__}: {str(e)[:150]}")
                traceback.print_exc()
                try:
                    if session.alive():
                        await safe_screenshot(session.page, f"crash_{name}")
                except Exception:
                    pass
                results.append((name, finalize_item(name, error=f"{type(e).__name__}: {str(e)[:150]}")))

        CTX["items"] = [item for _, item in results]

        print("\n" + "=" * 60)
        print("导出完成 - 结果汇总")
        print("=" * 60)
        success = 0
        for name, item in results:
            status = item["status"]
            if status != "FAIL":
                success += 1
            icon = "✓" if status != "FAIL" else "✗"
            log_info(f"  [{status}] {icon} {name}: {item.get('file') or '未生成'}")
        log_info(f"\n成功 {success}/{len(results)} | 输出目录: {OUTPUT_DIR}")

        ok_count = sum(1 for i in CTX["items"] if i["status"] != "FAIL")
        fallback_count = sum(1 for i in CTX["items"] if i["status"] == "OK(兜底)")
        fail_count = sum(1 for i in CTX["items"] if i["status"] == "FAIL")
        manifest_path = write_manifest(
            OUTPUT_DIR, "douyin", CTX["items"],
            {"ok": ok_count, "fallback": fallback_count, "fail": fail_count},
        )
        log_info(f"  → manifest 已保存: {os.path.basename(manifest_path)}")

        if success == len(results):
            log_info("🎉 抖音全部数据导出成功！")
        elif success > 0:
            log_info(f"⚠ 抖音部分导出成功（{len(results) - success}项失败）")
        else:
            log_info("✗ 抖音全部导出失败")

        # L3 指纹基线：只在「全部成功且未用兜底」时更新
        if success == len(results) and not CTX["fallback"] and CTX["fingerprint_found"]:
            write_fingerprint(FINGERPRINT_PATH, CTX["fingerprint_found"])
            log_info(f"  → 页面指纹基线已更新: {len(CTX['fingerprint_found'])} 项")
        elif CTX["fallback"]:
            log_info("  → 本次用过兜底策略，指纹基线不更新（待人工确认）")

        await session.stop()

        # 退出码必须如实反映结果：面板/launcher 靠退出码显示 OK/FAIL
        if success < len(results):
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
