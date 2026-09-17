#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CoinGlass 涨幅榜监控脚本 - 云端版 (GitHub Actions)
功能：每小时读取 CoinGlass 涨幅榜前十，记录上榜时间、已上榜时长、上榜后涨幅
运行环境：GitHub Actions + Playwright Chromium (Linux)
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from playwright.async_api import async_playwright

# ============== 配置区 ==============
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "gainers_data.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "gainers_report.txt")
HTML_FILE = os.path.join(SCRIPT_DIR, "docs", "index.html")
LOG_FILE = os.path.join(SCRIPT_DIR, "monitor.log")
TOP_N = 10
# ====================================

TZ = timezone(timedelta(hours=8))


def get_now():
    return datetime.now(TZ)


def get_now_str():
    return get_now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg):
    line = f"[{get_now_str()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except IOError:
        pass


async def fetch_gainers():
    """使用 Playwright Chromium 抓取 CoinGlass 24小时涨幅榜"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="zh-CN",
            viewport={"width": 1920, "height": 1080}
        )

        page = await context.new_page()

        try:
            log("正在访问 CoinGlass 涨跌榜页面...")
            await page.goto(
                "https://www.coinglass.com/zh/gainers-losers",
                wait_until="networkidle",
                timeout=60000
            )
            await page.wait_for_timeout(3000)

            # 确保选中24小时标签
            try:
                time_24h = page.get_by_text("24小时", exact=True)
                if await time_24h.count() > 0:
                    await time_24h.first.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

            rows = await page.query_selector_all("table tbody tr")
            raw_gainers = []
            debug_count = 0

            for row in rows:
                cells = await row.query_selector_all("td")
                if len(cells) < 4:
                    continue

                # 获取行完整HTML，提取所有交易所图标文件名
                row_html = await row.inner_html()
                row_html_lower = row_html.lower()
                # 提取 static/exchanges/ 后面的文件名
                exchange_icons = re.findall(r'static/exchanges/([a-z0-9_-]+)\.png', row_html_lower)
                has_binance = "binance" in row_html_lower or "binance" in exchange_icons

                # 调试：输出前5行的交易所图标列表和HTML长度
                if debug_count < 5:
                    log(f"[调试] 行{debug_count+1} 交易所图标={exchange_icons} 含币安={has_binance} HTML长度={len(row_html)}")
                    debug_count += 1

                cell_texts = []
                for cell in cells:
                    text = await cell.inner_text()
                    cell_texts.append(text.strip())

                try:
                    rank = int(cell_texts[0])
                    symbol = cell_texts[1]
                    price_str = cell_texts[2].replace("$", "").replace(",", "")
                    change_str = cell_texts[3].replace("%", "").replace("+", "")

                    price = float(price_str)
                    change = float(change_str)
                    volume = cell_texts[4] if len(cells) > 4 else ""

                    # 多抓取候选（前30名），过滤后取前10
                    if change > 0 and rank <= 30:
                        raw_gainers.append((rank, symbol, price, change, volume, has_binance))
                except (ValueError, IndexError):
                    continue

            # 过滤：仅保留币安永续合约品种，然后重新排名
            binance_gainers = [g for g in raw_gainers if g[5]]
            non_binance = [g[1] for g in raw_gainers if not g[5]]
            log(f"涨幅榜候选{len(raw_gainers)}个，其中币安永续合约{len(binance_gainers)}个")
            if non_binance:
                log(f"过滤掉非币安品种: {', '.join(non_binance)}")

            # 重新排名（从1开始），取前 TOP_N
            gainers = [(i + 1, g[1], g[2], g[3], g[4]) for i, g in enumerate(binance_gainers[:TOP_N])]
            log(f"最终涨幅榜 Top{len(gainers)}（仅币安永续合约）")
            return gainers

        except Exception as e:
            log(f"抓取异常: {type(e).__name__}: {e}")
            return []
        finally:
            await browser.close()


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"coins": {}, "last_update": None, "history": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log(f"加载数据失败，使用空数据: {e}")
        return {"coins": {}, "last_update": None, "history": []}


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log(f"数据已保存")
    except IOError as e:
        log(f"保存数据失败: {e}")


def format_duration(delta):
    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}小时{minutes}分"
    else:
        return f"{minutes}分钟"


def format_price(price):
    if price >= 100:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:,.4f}"
    elif price >= 0.01:
        return f"${price:.6f}"
    else:
        return f"${price:.8f}"


def update_and_report(gainers, data):
    now = get_now()
    now_str = get_now_str()
    current_symbols = set()

    lines = []
    lines.append("=" * 100)
    lines.append(f"  CoinGlass 涨幅榜监控报告  |  监控时间: {now_str}  |  24小时涨幅榜 Top{TOP_N}")
    lines.append("=" * 100)
    lines.append("")
    lines.append(
        f"{'排名':<4} {'币种':<14} {'价格':<14} {'24h涨幅':<10} "
        f"{'首次上榜时间':<18} {'已上榜时长':<12} {'上榜后涨幅':<10} {'状态'}"
    )
    lines.append("-" * 100)

    for rank, symbol, price, change, volume in gainers:
        current_symbols.add(symbol)
        is_new = False

        if symbol not in data["coins"]:
            is_new = True
            data["coins"][symbol] = {
                "symbol": symbol,
                "first_seen": now_str,
                "first_seen_price": price,
                "first_seen_change": change,
                "last_seen": now_str,
                "current_price": price,
                "current_change": change,
                "best_rank": rank,
                "appearances": 1,
                "rank_history": [
                    {"time": now_str, "rank": rank, "price": price, "change": change}
                ],
            }
        else:
            coin = data["coins"][symbol]
            coin["last_seen"] = now_str
            coin["current_price"] = price
            coin["current_change"] = change
            coin["appearances"] = coin.get("appearances", 1) + 1
            if rank < coin.get("best_rank", 999):
                coin["best_rank"] = rank
            coin["rank_history"].append(
                {"time": now_str, "rank": rank, "price": price, "change": change}
            )
            if len(coin["rank_history"]) > 200:
                coin["rank_history"] = coin["rank_history"][-200:]

        coin = data["coins"][symbol]

        first_seen_time = datetime.strptime(coin["first_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        duration = now - first_seen_time
        duration_str = format_duration(duration)

        first_price = coin["first_seen_price"]
        gain_after_listing = ((price - first_price) / first_price * 100) if first_price > 0 else 0.0
        gain_str = f"{gain_after_listing:+.2f}%"
        status = "新上榜" if is_new else "持续在榜"

        lines.append(
            f"{rank:<4} {symbol:<14} {format_price(price):<14} {change:+.2f}%{'':<4} "
            f"{coin['first_seen']:<18} {duration_str:<12} {gain_str:<10} {status}"
        )

    dropped = []
    last_update = data.get("last_update")
    if last_update:
        for symbol, coin in data["coins"].items():
            if symbol not in current_symbols and coin.get("last_seen") == last_update:
                dropped.append(symbol)

    if dropped:
        lines.append("")
        lines.append(f"  本轮掉出涨幅榜 Top{TOP_N}: {', '.join(dropped)}")

    lines.append("")
    lines.append("-" * 100)
    lines.append("监控统计:")

    long_listing = []
    for symbol, coin in data["coins"].items():
        if symbol in current_symbols:
            first_seen_time = datetime.strptime(coin["first_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            if (now - first_seen_time).total_seconds() >= 3600:
                long_listing.append((symbol, coin))

    if long_listing:
        lines.append(f"  在榜超过1小时的币种 ({len(long_listing)}个):")
        for symbol, coin in sorted(long_listing, key=lambda x: x[1].get("best_rank", 99)):
            first_price = coin["first_seen_price"]
            cur_price = coin["current_price"]
            gain = ((cur_price - first_price) / first_price * 100) if first_price > 0 else 0
            first_seen_time = datetime.strptime(coin["first_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            dur = format_duration(now - first_seen_time)
            lines.append(
                f"    - {symbol}: 首次上榜 {coin['first_seen']}, "
                f"已在榜 {dur}, 上榜后涨幅 {gain:+.2f}%, 最高排名 #{coin.get('best_rank', 'N/A')}"
            )

    new_coins = [s for s in current_symbols if data["coins"][s]["first_seen"] == now_str]
    if new_coins:
        lines.append(f"  本轮新上榜币种 ({len(new_coins)}个): {', '.join(sorted(new_coins))}")

    lines.append("")
    lines.append("=" * 100)

    data["last_update"] = now_str

    snapshot = {
        "time": now_str,
        "gainers": [
            {"rank": r, "symbol": s, "price": p, "change": c, "volume": v}
            for r, s, p, c, v in gainers
        ]
    }
    data.setdefault("history", []).append(snapshot)
    if len(data["history"]) > 720:
        data["history"] = data["history"][-720:]

    return "\n".join(lines)


def generate_html_report(gainers, data):
    """生成手机端友好的 HTML 报告"""
    now = get_now()
    now_str = get_now_str()
    current_symbols = set(s for _, s, _, _, _ in gainers)

    rows_html = []
    for rank, symbol, price, change, volume in gainers:
        coin = data["coins"].get(symbol, {})
        first_seen = coin.get("first_seen", now_str)
        first_price = coin.get("first_seen_price", price)

        try:
            first_seen_time = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
            duration = now - first_seen_time
            total_seconds = int(duration.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            duration_str = f"{hours}小时{minutes}分" if hours > 0 else f"{minutes}分钟"
        except Exception:
            duration_str = "-"

        gain_after = ((price - first_price) / first_price * 100) if first_price > 0 else 0.0
        gain_class = "up" if gain_after >= 0 else "down"
        change_class = "up" if change >= 0 else "down"

        is_new = first_seen == now_str
        status_badge = '<span class="badge new">新上榜</span>' if is_new else '<span class="badge keep">持续在榜</span>'
        rank_class = "rank-top" if rank <= 3 else ""

        rows_html.append(f"""
        <div class="coin-row">
            <div class="rank {rank_class}">{rank}</div>
            <div class="coin-info">
                <div class="coin-name">{symbol} {status_badge}</div>
                <div class="coin-price">{format_price(price)}</div>
                <div class="coin-duration">⏱ 已在榜 {duration_str}</div>
            </div>
            <div class="metrics">
                <div class="metric">
                    <span class="label">24h涨幅</span>
                    <span class="value {change_class}">{change:+.2f}%</span>
                </div>
                <div class="metric">
                    <span class="label">上榜后</span>
                    <span class="value {gain_class}">{gain_after:+.2f}%</span>
                </div>
            </div>
            <div class="time-info">
                <div class="time-item"><span class="label">首次上榜</span><span class="val">{first_seen}</span></div>
                <div class="time-item"><span class="label">已在榜</span><span class="val">{duration_str}</span></div>
            </div>
        </div>""")

    dropped = []
    last_update = data.get("last_update")
    if last_update:
        for symbol, coin in data["coins"].items():
            if symbol not in current_symbols and coin.get("last_seen") == last_update:
                dropped.append(symbol)
    dropped_html = f'<div class="dropped">📉 本轮掉出 Top{TOP_N}: {", ".join(dropped)}</div>' if dropped else ""

    long_listing = []
    for symbol, coin in data["coins"].items():
        if symbol in current_symbols:
            try:
                first_seen_time = datetime.strptime(coin["first_seen"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
                if (now - first_seen_time).total_seconds() >= 3600:
                    long_listing.append((symbol, coin))
            except Exception:
                pass

    stats_html = ""
    if long_listing:
        items = ""
        for symbol, coin in sorted(long_listing, key=lambda x: x[1].get("best_rank", 99)):
            fp = coin["first_seen_price"]
            cp = coin["current_price"]
            g = ((cp - fp) / fp * 100) if fp > 0 else 0
            gc = "up" if g >= 0 else "down"
            items += f'<div class="stat-item"><span class="stat-symbol">{symbol}</span><span class="stat-gain {gc}">{g:+.2f}%</span><span class="stat-rank">最高#{coin.get("best_rank","-")}</span></div>'
        stats_html = f"""
        <div class="stats-section">
            <div class="stats-title">⏱ 在榜超过1小时 ({len(long_listing)}个)</div>
            {items}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta http-equiv="refresh" content="300">
<title>CoinGlass 涨幅榜监控</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f1419;
    color: #e8e8e8;
    min-height: 100vh;
    padding: 12px;
}}
.header {{
    text-align: center;
    padding: 16px 0 20px;
}}
.header h1 {{
    font-size: 20px;
    font-weight: 700;
    background: linear-gradient(135deg, #f7931a, #ff6b6b);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 6px;
}}
.header .time {{
    font-size: 13px;
    color: #8899a6;
}}
.header .refresh {{
    font-size: 11px;
    color: #5c6c7c;
    margin-top: 4px;
}}
.coin-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
}}
.coin-row {{
    background: #1a2332;
    border-radius: 12px;
    padding: 14px;
    display: flex;
    align-items: center;
    gap: 12px;
    border-left: 3px solid #2a3a4a;
}}
.rank {{
    width: 32px;
    height: 32px;
    border-radius: 8px;
    background: #2a3a4a;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 15px;
    flex-shrink: 0;
}}
.rank-top {{
    background: linear-gradient(135deg, #f7931a, #ff6b35);
    color: #fff;
}}
.coin-info {{ flex: 1; min-width: 0; }}
.coin-name {{
    font-size: 16px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
}}
.coin-price {{
    font-size: 13px;
    color: #8899a6;
    margin-top: 2px;
}}
.coin-duration {{
    font-size: 11px;
    color: #60a5fa;
    margin-top: 3px;
    font-weight: 500;
}}
.badge {{
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 500;
}}
.badge.new {{ background: #1a5f3f; color: #4ade80; }}
.badge.keep {{ background: #1e3a5f; color: #60a5fa; }}
.metrics {{
    display: flex;
    flex-direction: column;
    gap: 4px;
    text-align: right;
    flex-shrink: 0;
}}
.metric {{ display: flex; flex-direction: column; }}
.metric .label {{ font-size: 10px; color: #5c6c7c; }}
.metric .value {{ font-size: 14px; font-weight: 600; }}
.up {{ color: #ff4d4f; }}
.down {{ color: #52c41a; }}
.time-info {{ display: none; }}
.dropped {{
    background: #2a1a1a;
    border: 1px solid #4a2a2a;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    color: #ff7875;
    margin-top: 12px;
}}
.stats-section {{
    background: #1a2332;
    border-radius: 12px;
    padding: 14px;
    margin-top: 12px;
}}
.stats-title {{
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 10px;
    color: #d0d0d0;
}}
.stat-item {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid #2a3a4a;
    font-size: 13px;
}}
.stat-item:last-child {{ border-bottom: none; }}
.stat-symbol {{ font-weight: 600; }}
.stat-gain {{ font-weight: 600; }}
.stat-rank {{ color: #8899a6; font-size: 12px; }}
.footer {{
    text-align: center;
    padding: 20px 0 10px;
    font-size: 11px;
    color: #5c6c7c;
}}
@media (min-width: 600px) {{
    body {{ max-width: 700px; margin: 0 auto; }}
    .coin-duration {{ display: none; }}
    .time-info {{
        display: flex;
        flex-direction: column;
        gap: 2px;
        text-align: right;
        flex-shrink: 0;
    }}
    .time-item {{ display: flex; flex-direction: column; }}
    .time-item .label {{ font-size: 10px; color: #5c6c7c; }}
    .time-item .val {{ font-size: 11px; color: #8899a6; }}
}}
</style>
</head>
<body>
<div class="header">
    <h1>🔥 CoinGlass 涨幅榜监控</h1>
    <div class="time">更新时间: {now_str}</div>
    <div class="refresh">页面每 5 分钟自动刷新 · 云端每小时更新</div>
</div>
<div class="coin-list">
    {"".join(rows_html)}
</div>
{dropped_html}
{stats_html}
<div class="footer">
    数据来源: CoinGlass · GitHub Actions 每小时自动监控 · 24h 涨幅榜 Top{TOP_N}
</div>
</body>
</html>"""
    return html


async def main():
    log("=" * 60)
    log("开始执行 CoinGlass 涨幅榜监控任务 (云端版)")

    # 确保docs目录存在
    os.makedirs(os.path.dirname(HTML_FILE), exist_ok=True)

    gainers = await fetch_gainers()
    if not gainers:
        log("警告：当前涨幅榜无币安永续合约品种上榜，生成本次空报告")
        # 生成空报告，不退出
        empty_report = f"{'='*60}\n  CoinGlass 涨幅榜监控报告 | {get_now_str()}\n  （当前涨幅榜无币安永续合约品种上榜）\n{'='*60}\n"
        try:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(empty_report)
        except IOError:
            pass
        data = load_data()
        try:
            html_report = generate_html_report([], data)
            with open(HTML_FILE, "w", encoding="utf-8") as f:
                f.write(html_report)
            log("HTML报告已保存（空榜单）")
        except Exception as e:
            log(f"生成HTML报告失败: {e}")
        save_data(data)
        log("监控任务执行完成（空榜单）")
        return

    data = load_data()
    report = update_and_report(gainers, data)

    print()
    print(report)

    # 保存文本报告
    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(report)
        log("文本报告已保存")
    except IOError as e:
        log(f"保存文本报告失败: {e}")

    # 保存HTML报告
    try:
        html_report = generate_html_report(gainers, data)
        with open(HTML_FILE, "w", encoding="utf-8") as f:
            f.write(html_report)
        log("HTML报告已保存")
    except Exception as e:
        log(f"生成HTML报告失败: {e}")

    save_data(data)
    log("监控任务执行完成")


if __name__ == "__main__":
    asyncio.run(main())

