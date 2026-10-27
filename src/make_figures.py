# src/make_figures.py
# 生成技术报告中的两张数据图
# 输出: docs/report/figures/fig2_retrieval.png, fig3_ablation.png

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

# 设置中文字体（Windows 系统）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = Path(__file__).parent.parent / "docs" / "report" / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_fig2():
    """检索指标对比柱状图"""
    labels = ["文件级 Top-1", "方法级 Top-1", "方法级 Top-5"]
    rule = [66.7, 83.3, 100.0]
    rule_llm = [100.0, 100.0, 100.0]

    x = np.arange(len(labels))
    width = 0.35

    fig, ax = plt.subplots(figsize=(8, 5))
    bars1 = ax.bar(x - width/2, rule, width, label="规则检索器", color="#888888")
    bars2 = ax.bar(x + width/2, rule_llm, width, label="规则 + LLM 重排", color="#2E75B6")

    for bars in (bars1, bars2):
        for bar in bars:
            height = bar.get_height()
            ax.annotate(f"{height:.1f}%",
                        xy=(bar.get_x() + bar.get_width()/2, height),
                        xytext=(0, 3), textcoords="offset points",
                        ha="center", va="bottom", fontsize=10)

    ax.set_ylabel("命中率 (%)", fontsize=12)
    ax.set_title("图 2  规则检索器 vs 规则 + LLM 重排的检索指标", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylim(0, 115)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    out = OUT_DIR / "fig2_retrieval.png"
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(f"已保存: {out}")


def make_fig3():
    """消融实验对比图（分组柱状图）"""
    labels = ["完整系统", "- strings", "- calls", "- name", "- annotation"]
    file_top1 = [80.0, 60.0, 80.0, 80.0, 80.0]
    method_top1 = [80.0, 80.0, 80.0, 100.0, 80.0]
    method_top5 = [100.0, 80.0, 80.0, 100.0, 100.0]

    x = np.arange(len(labels))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.bar(x - width, file_top1, width, label="文件级 Top-1", color="#888888")
    ax.bar(x, method_top1, width, label="方法级 Top-1", color="#2E75B6")
    ax.bar(x + width, method_top5, width, label="方法级 Top-5", color="#ED7D31")

    # 标注数字
    for xi, v in zip(x - width, file_top1):
        ax.annotate(f"{v:.0f}", xy=(xi, v), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=9)
    for xi, v in zip(x, method_top1):
        ax.annotate(f"{v:.0f}", xy=(xi, v), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=9)
    for xi, v in zip(x + width, method_top5):
        ax.annotate(f"{v:.0f}", xy=(xi, v), xytext=(0, 3),
                    textcoords="offset points", ha="center", fontsize=9)

    # 突出 name 信号的反直觉发现
    name_idx = 3
    ax.axvspan(x[name_idx] - 0.4, x[name_idx] + 0.4, color="yellow", alpha=0.15)
    ax.annotate("关掉后 Top-1 反而涨",
                xy=(x[name_idx], 105),
                xytext=(x[name_idx], 112),
                ha="center", fontsize=10, color="red",
                arrowprops=dict(arrowstyle="->", color="red"))

    ax.set_ylabel("命中率 (%)", fontsize=12)
    ax.set_title("图 3  消融实验：关闭各信号后的指标变化", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11)
    ax.set_ylim(0, 120)
    ax.legend(fontsize=11)
    ax.grid(axis="y", alpha=0.3)

    out = OUT_DIR / "fig3_ablation.png"
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)
    print(f"已保存: {out}")


if __name__ == "__main__":
    make_fig2()
    make_fig3()
    print(f"\n全部图片已保存到: {OUT_DIR}")