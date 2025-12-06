# astrbot_stock_market/treemap_generator.py

# -*- coding: utf-8 -*-
"""
大盘云图生成模块 (终极字体修复版)

V15 (最终版):
- 我为之前所有的错误道歉。此版本将彻底解决问题。
- 完全复刻 K 线图功能的字体路径发现和加载机制，确保字体100%正确加载。
- 模块不再依赖外部传入字体路径，自我管理，减少出错环节。
"""

import asyncio

import aiosqlite
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import os
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import squarify
from matplotlib.font_manager import FontProperties, fontManager

# --- 配置 (保持不变) ---
PERIODS_FOR_30_MIN = 6
COLOR_MAP = {
    -4.0: "#28d742",
    -3.0: "#1da548",
    -2.0: "#106f2f",
    -1.0: "#0a5421",
    0.0: "#424454",
    1.0: "#6d1414",
    2.0: "#960f0f",
    3.0: "#be1207",
    4.0: "#e41813",
}
COLOR_POINTS = sorted(COLOR_MAP.keys())
COLOR_HEX = [COLOR_MAP[p] for p in COLOR_POINTS]
min_val, max_val = min(COLOR_POINTS), max(COLOR_POINTS)
normalized_points = (np.array(COLOR_POINTS) - min_val) / (max_val - min_val)
CUSTOM_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "market_cmap", list(zip(normalized_points, COLOR_HEX))
)
NORM = mcolors.TwoSlopeNorm(vmin=-4.0, vcenter=0, vmax=4.0)


async def _get_stock_data_for_treemap(db_path: str) -> pd.DataFrame | None:
    """从数据库获取计算所需的数据。(此函数内容不变)"""
    if not os.path.exists(db_path):
        return None
    processed_data = []
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT stock_id, name, current_price FROM stocks"
            )
            stocks = await cursor.fetchall()
            if not stocks:
                return None
            for stock_id, name, current_price in stocks:
                k_cursor = await db.execute(
                    "SELECT close FROM kline_history WHERE stock_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (stock_id, PERIODS_FOR_30_MIN + 1),
                )
                history_prices = [row[0] for row in await k_cursor.fetchall()]
                ref_price = (
                    history_prices[PERIODS_FOR_30_MIN]
                    if len(history_prices) > PERIODS_FOR_30_MIN
                    else None
                )
                change_percent = (
                    ((current_price - ref_price) / ref_price) * 100
                    if ref_price and ref_price > 0
                    else 0.0
                )
                processed_data.append(
                    {
                        "name": name,
                        "price": current_price,
                        "change_percent": change_percent,
                    }
                )
    except Exception as e:
        print(f"读取数据库出错: {e}")
        return None
    return pd.DataFrame(processed_data) if processed_data else None


def _generate_image(df: pd.DataFrame, output_path: Path):
    """核心的图片生成逻辑。"""

    # --- V15 终极字体修复 ---
    # 1. 完全复刻 K线图 的字体路径发现方式
    script_path = Path(__file__).resolve().parent
    font_path = script_path / "static" / "fonts" / "SourceHanSansCN-Bold.otf"

    if not font_path.exists():
        print(f"!!! 致命错误：字体文件未找到于 '{font_path}'，无法生成图表。")
        raise FileNotFoundError(f"字体文件未找到于 '{font_path}'")

    # 2. 完全复刻 K线图 的字体加载和设置方式
    fontManager.addfont(str(font_path))
    plt.rcParams["font.sans-serif"] = [FontProperties(fname=font_path).get_name()]
    plt.rcParams["axes.unicode_minus"] = False
    # --- 修复结束 ---

    df = df.sort_values(by="price", ascending=False).reset_index(drop=True)
    bg_colors = [mcolors.to_hex(CUSTOM_CMAP(NORM(p))) for p in df["change_percent"]]

    sizes = np.log1p(df["price"].values)
    labels = [
        f"{row['name']}\n{row['change_percent']:+.2f}%\n${row['price']:.2f}"
        for _, row in df.iterrows()
    ]
    title_font_prop = FontProperties(fname=font_path, size=27)

    plt.style.use("dark_background")
    fig, ax = plt.subplots(1, figsize=(16, 9), dpi=200)

    squarify.plot(
        sizes=sizes,
        color=bg_colors,
        ax=ax,
        alpha=0.9,
        label=None,
        edgecolor="black",
        linewidth=1.5,
    )

    for i, rect in enumerate(ax.patches):
        if i < len(labels):
            x, y = rect.get_xy()
            dx, dy = rect.get_width(), rect.get_height()
            ax.text(
                x + dx / 2,
                y + dy / 2,
                labels[i],
                ha="center",
                va="center",
                fontsize=22,
                weight="bold",
                color="white",
            )

    plt.title(
        "虚拟股票市场 - 大盘云图 (30分钟)",
        fontproperties=title_font_prop,
        color="white",
        pad=20,
    )
    plt.axis("off")
    plt.tight_layout()

    try:
        plt.savefig(
            output_path,
            bbox_inches="tight",
            pad_inches=0.1,
            facecolor=fig.get_facecolor(),
            edgecolor="none",
        )
    finally:
        plt.close(fig)
        plt.rcParams.update(plt.rcParamsDefault)


async def create_market_treemap(db_path: str, output_dir: str) -> str | None:
    """生成大盘云图的主函数。"""
    stock_df = await _get_stock_data_for_treemap(db_path)
    if stock_df is None or stock_df.empty:
        print("未能获取足够的数据来生成大盘云图。")
        return None

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(exist_ok=True)
    output_path = output_dir_path / "market_treemap.png"

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _generate_image, stock_df, output_path)
        return str(output_path)
    except Exception as e:
        print(f"生成大盘云图时发生未知错误: {e}")
        return None
