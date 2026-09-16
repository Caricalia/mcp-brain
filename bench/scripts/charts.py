#!/usr/bin/env python3
"""Generates all bench charts from retrieval_raw.csv / retrieval_summary.csv."""
import csv
import os
import random
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np

from lib import BENCH
from style import apply_base_style, style_axes, add_titles, add_footer, save_fig, ENTITY_COLOR, PALETTE, TEXT_SECONDARY, TEXT_PRIMARY, GRID

RESULTS_DIR = os.path.join(BENCH, "results")
CHARTS_DIR = os.path.join(BENCH, "charts")

DEFAULT_CFG = ("semantic", "0.3", "", "8")
RECOMMENDED_CFG = ("semantic", "0.5", "0.1", "8")
CANDIDATE2_CFG = ("semantic", "0.45", "0.1", "8")
TEXT_CFG_L8 = ("text", "", "", "8")

QTYPE_ORDER = ["paraphrase", "keyword", "multi", "negative"]
QTYPE_LABEL = {"paraphrase": "Paraphrase", "keyword": "Keyword", "multi": "Multi-fact", "negative": "Negative"}


def load_raw():
    with open(os.path.join(RESULTS_DIR, "retrieval_raw.csv")) as f:
        return list(csv.DictReader(f))


def load_summary():
    with open(os.path.join(RESULTS_DIR, "retrieval_summary.csv")) as f:
        return list(csv.DictReader(f))


def load_text_keywords_recall():
    with open(os.path.join(RESULTS_DIR, "text_keywords_retrieval.csv")) as f:
        rows = list(csv.DictReader(f))
    return [float(r["recall"]) for r in rows]


def cfg_of(r):
    return (r["mode"], r["min_similarity"], r["delta"], r["limit"])


def bootstrap_ci(values, n_boot=1000, seed=42):
    rng = random.Random(seed)
    vals = list(values)
    n = len(vals)
    if n == 0:
        return (0.0, 0.0, 0.0)
    means = []
    for _ in range(n_boot):
        sample = [vals[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    mean = sum(vals) / n
    return (mean, lo, hi)


def rows_for_cfg(raw, cfg, qtype=None):
    out = []
    for r in raw:
        if cfg_of(r) == cfg and (qtype is None or r["query_type"] == qtype):
            out.append(r)
    return out


def cfg_label(cfg):
    mode, ms, delta, limit = cfg
    if mode == "text":
        return f"text (limit={limit})"
    d = delta if delta else "no cutoff"
    return f"sem. min_sim={ms}, delta={d}, limit={limit}"


# ---------------- Chart 1: Recall vs tokens Pareto ----------------

def chart_pareto(summary):
    apply_base_style()
    all_rows = [r for r in summary if r["query_type"] == "ALL"]
    pts = [(float(r["mean_est_tokens"]), float(r["mean_recall"]), r) for r in all_rows]

    # Pareto front: maximize recall, minimize tokens
    pts_sorted = sorted(pts, key=lambda p: p[0])
    front = []
    best_recall = -1
    for tok, rec, r in pts_sorted:
        if rec > best_recall:
            front.append((tok, rec, r))
            best_recall = rec

    fig, ax = plt.subplots(figsize=(12, 6.75))
    style_axes(ax)

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ax.scatter(xs, ys, s=26, color=TEXT_SECONDARY, alpha=0.35, linewidths=0, label="All configurations", zorder=2)

    fx = [p[0] for p in front]
    fy = [p[1] for p in front]
    ax.plot(fx, fy, color=PALETTE["violet"], linewidth=2, zorder=3, label="Pareto frontier")
    ax.scatter(fx, fy, s=30, color=PALETTE["violet"], zorder=4)

    def mark(cfg, color, label, dx, dy):
        row = [r for r in all_rows if cfg_of(r) == cfg][0]
        tok, rec = float(row["mean_est_tokens"]), float(row["mean_recall"])
        ax.scatter([tok], [rec], s=170, color=color, edgecolor="white", linewidth=1.5, zorder=6)
        ax.annotate(label, (tok, rec), xytext=(tok + dx, rec + dy), fontsize=10, fontweight="bold",
                    color=color, zorder=7)
        return tok, rec

    mark(DEFAULT_CFG, ENTITY_COLOR["current_default"], "Current config.\n(min_sim 0.3, limit 8)", 25, -0.03)
    mark(RECOMMENDED_CFG, ENTITY_COLOR["recommended"], "Recommended\n(min_sim 0.5, Δ0.1, limit 8)", 25, 0.02)
    mark(TEXT_CFG_L8, ENTITY_COLOR["text_mode"], "Text mode\n(full question)", 25, -0.03)

    # Text with keywords (realistic variant): same text mechanism, query already reduced to keywords.
    kw_recalls = load_text_keywords_recall()
    kw_recall = sum(kw_recalls) / len(kw_recalls)
    text_row = [r for r in all_rows if cfg_of(r) == TEXT_CFG_L8][0]
    kw_tokens = float(text_row["mean_est_tokens"])
    ax.scatter([kw_tokens], [kw_recall], s=170, color=PALETTE["blue"], marker="D",
               edgecolor="white", linewidth=1.5, zorder=6)
    ax.annotate("Text mode\n(keywords)", (kw_tokens, kw_recall), xytext=(kw_tokens + 25, kw_recall - 0.10),
                fontsize=10, fontweight="bold", color=PALETTE["blue"], zorder=7)

    ax.set_xlabel("Mean response tokens (heuristic: characters / 3.5)")
    ax.set_ylabel("Mean recall (fraction of target memories retrieved)")
    ax.set_ylim(0, 1.0)
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    add_titles(fig, "Recall can be matched with far fewer tokens",
               "Recall vs. estimated tokens per query, one point per recall configuration")
    add_footer(fig)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.82, bottom=0.14)
    save_fig(fig, os.path.join(CHARTS_DIR, "01_pareto_recall_tokens"))


# ---------------- Chart 2: heatmaps min_similarity x delta at limit=8 ----------------

def chart_heatmaps(summary):
    apply_base_style()
    min_sims = [0.2, 0.3, 0.35, 0.4, 0.45, 0.5]
    deltas = [None, 0.1, 0.15, 0.2, 0.25, 0.3]
    delta_labels = ["no cutoff", "0.10", "0.15", "0.20", "0.25", "0.30"]

    def get(ms, d, field):
        d_str = "" if d is None else str(d)
        for r in summary:
            if r["query_type"] == "ALL" and r["mode"] == "semantic" and r["min_similarity"] == str(ms) \
               and r["delta"] == d_str and r["limit"] == "8":
                return float(r[field])
        return np.nan

    for field, fname, title, cbar_label, fmt in [
        ("mean_recall", "02a_heatmap_recall", "Recall depends little on the relative cutoff, a lot on the threshold",
         "Mean recall", "{:.2f}"),
        ("mean_est_tokens", "02b_heatmap_tokens", "The relative cutoff (delta) is the lever that cuts tokens the most",
         "Mean tokens", "{:.0f}"),
    ]:
        mat = np.array([[get(ms, d, field) for d in deltas] for ms in min_sims])
        fig, ax = plt.subplots(figsize=(12, 6.75))
        cmap = plt.get_cmap("Blues")
        im = ax.imshow(mat, cmap=cmap, aspect="auto")
        ax.set_xticks(range(len(deltas)))
        ax.set_xticklabels(delta_labels)
        ax.set_yticks(range(len(min_sims)))
        ax.set_yticklabels([f"{m:.2f}" for m in min_sims])
        ax.set_xlabel("Relative cutoff delta (limit=8)")
        ax.set_ylabel("min_similarity")
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                v = mat[i, j]
                txt_color = "white" if (v - np.nanmin(mat)) / (np.nanmax(mat) - np.nanmin(mat) + 1e-9) > 0.6 else TEXT_PRIMARY
                ax.text(j, i, fmt.format(v), ha="center", va="center", fontsize=9, color=txt_color)
        ax.spines[:].set_visible(False)
        ax.grid(False)
        cbar = fig.colorbar(im, ax=ax, shrink=0.85)
        cbar.set_label(cbar_label, color=TEXT_SECONDARY)
        cbar.ax.yaxis.set_tick_params(color=TEXT_SECONDARY)
        add_titles(fig, title, "Mean over the supplied queries, limit=8 fixed, semantic mode")
        add_footer(fig)
        fig.subplots_adjust(left=0.10, right=0.97, top=0.82, bottom=0.12)
        save_fig(fig, os.path.join(CHARTS_DIR, fname))


# ---------------- Chart 3: bars by query type ----------------

def chart_by_type(summary):
    apply_base_style()
    cfgs = [
        (TEXT_CFG_L8, "Text mode (limit 8)", ENTITY_COLOR["text_mode"]),
        (DEFAULT_CFG, "Current config. (0.3, limit 8)", ENTITY_COLOR["current_default"]),
        (RECOMMENDED_CFG, "Recommended (0.5, Δ0.1, limit 8)", ENTITY_COLOR["recommended"]),
        (CANDIDATE2_CFG, "Candidate 2 (0.45, Δ0.1, limit 8)", ENTITY_COLOR["candidate_2"]),
    ]

    def get(cfg, qtype):
        d_str = cfg[2]
        for r in summary:
            if r["query_type"] == qtype and cfg_of(r) == cfg:
                return float(r["mean_recall"])
        return 0.0

    fig, ax = plt.subplots(figsize=(12, 6.75))
    style_axes(ax)
    n_types = len(QTYPE_ORDER)
    n_cfgs = len(cfgs)
    width = 0.19
    x = np.arange(n_types)
    for i, (cfg, label, color) in enumerate(cfgs):
        vals = [get(cfg, qt) for qt in QTYPE_ORDER]
        offset = (i - (n_cfgs - 1) / 2) * width
        bars = ax.bar(x + offset, vals, width=width * 0.9, color=color, label=label, edgecolor=BG_EDGE if False else "white", linewidth=0.6)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v + 0.02, f"{v:.2f}", ha="center", va="bottom", fontsize=7.5, color=TEXT_SECONDARY)

    ax.set_xticks(x)
    ax.set_xticklabels([QTYPE_LABEL[q] for q in QTYPE_ORDER])
    ax.set_ylabel("Mean recall")
    ax.set_ylim(0, 1.15)
    ax.legend(loc="upper right", frameon=False, fontsize=8.5, ncol=2)
    add_titles(fig, "Paraphrased queries are the hardest for text mode",
               "Mean recall by query type and configuration (negative: trivial recall, see noise in chart 4)")
    add_footer(fig)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.12)
    save_fig(fig, os.path.join(CHARTS_DIR, "03_recall_by_query_type"))


BG_EDGE = "white"


# ---------------- Chart 4: noise on negative queries ----------------

def chart_noise(summary):
    apply_base_style()
    families = [
        ("text", None, None, "Text"),
    ]
    min_sims = [0.2, 0.3, 0.35, 0.4, 0.45, 0.5]

    fig, ax = plt.subplots(figsize=(12, 6.75))
    style_axes(ax)

    labels = []
    vals = []
    colors = []

    # text mode limit 8
    for r in summary:
        if cfg_of(r) == TEXT_CFG_L8 and r["query_type"] == "ALL":
            labels.append("Text\n(limit 8)")
            vals.append(float(r["mean_n_returned_negative"]) if r["mean_n_returned_negative"] not in ("", None) else 0.0)
            colors.append(ENTITY_COLOR["text_mode"])

    for ms in min_sims:
        for r in summary:
            if r["mode"] == "semantic" and r["min_similarity"] == str(ms) and r["delta"] == "" and r["limit"] == "8" and r["query_type"] == "ALL":
                labels.append(f"sem.\nmin_sim={ms}")
                v = r["mean_n_returned_negative"]
                vals.append(float(v) if v not in ("", None) else 0.0)
                colors.append(ENTITY_COLOR["current_default"] if ms == 0.3 else (ENTITY_COLOR["recommended"] if ms == 0.5 else TEXT_SECONDARY))

    x = np.arange(len(labels))
    bars = ax.bar(x, vals, color=colors, width=0.6, edgecolor="white", linewidth=0.6)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05, f"{v:.2f}", ha="center", va="bottom", fontsize=8.5, color=TEXT_SECONDARY)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=8.5)
    ax.set_ylabel("Mean results returned on queries WITHOUT a target memory")
    add_titles(fig, "Raising min_similarity reduces noise on unanswerable questions",
               "Mean results returned on the 8 negative queries, no relative cutoff, limit=8, by family")
    add_footer(fig)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.14)
    save_fig(fig, os.path.join(CHARTS_DIR, "04_noise_negative_queries"))


# ---------------- Chart 5: HERO ----------------

def chart_hero(raw, square=False):
    apply_base_style()
    cfgs = [
        (DEFAULT_CFG, "Current\nconfig.", ENTITY_COLOR["current_default"]),
        (RECOMMENDED_CFG, "Recom-\nmended", ENTITY_COLOR["recommended"]),
        (TEXT_CFG_L8, "Text\n(full\nquestion)", ENTITY_COLOR["text_mode"]),
    ]

    recall_stats = []
    token_stats = []
    for cfg, label, color in cfgs:
        rows = rows_for_cfg(raw, cfg)
        recalls = [float(r["recall"]) for r in rows]
        tokens = [float(r["est_tokens"]) for r in rows]
        recall_stats.append(bootstrap_ci(recalls))
        token_stats.append(bootstrap_ci(tokens))

    # Add the realistic keyword-text variant (recall from a separate CSV; tokens approximated
    # by the "texto (pregunta)" cost, same retrieval mechanism, shorter query).
    cfgs.append((None, "Text\n(key-\nwords)", PALETTE["blue"]))
    kw_recalls = load_text_keywords_recall()
    recall_stats.append(bootstrap_ci(kw_recalls))
    token_stats.append(token_stats[-1])

    figsize = (9, 9) if square else (12, 6.75)
    fig, axes = plt.subplots(1, 2, figsize=figsize)
    for ax in axes:
        style_axes(ax)

    x = np.arange(len(cfgs))
    labels = [c[1] for c in cfgs]
    colors = [c[2] for c in cfgs]

    ax = axes[0]
    means = [s[0] for s in recall_stats]
    los = [s[0] - s[1] for s in recall_stats]
    his = [s[2] - s[0] for s in recall_stats]
    bars = ax.bar(x, means, color=colors, width=0.55, edgecolor="white", linewidth=0.8)
    ax.errorbar(x, means, yerr=[los, his], fmt="none", ecolor=TEXT_PRIMARY, elinewidth=1.4, capsize=5, zorder=5)
    for xi, m, s in zip(x, means, recall_stats):
        label_y = min(s[2] + 0.03, 0.97)
        ax.text(xi, label_y, f"{m:.2f}", ha="center", fontsize=12, fontweight="bold", color=TEXT_PRIMARY)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("Mean recall")
    ax.set_title("Recall (95% CI, bootstrap)", fontsize=11, color=TEXT_SECONDARY)

    ax = axes[1]
    means = [s[0] for s in token_stats]
    los = [s[0] - s[1] for s in token_stats]
    his = [s[2] - s[0] for s in token_stats]
    bars = ax.bar(x, means, color=colors, width=0.55, edgecolor="white", linewidth=0.8)
    ax.errorbar(x, means, yerr=[los, his], fmt="none", ecolor=TEXT_PRIMARY, elinewidth=1.4, capsize=5, zorder=5)
    for xi, m, s in zip(x, means, token_stats):
        ax.text(xi, s[2] + max(means) * 0.04, f"{m:.0f}", ha="center", fontsize=13, fontweight="bold", color=TEXT_PRIMARY)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylim(0, max(s[2] for s in token_stats) * 1.18)
    ax.set_ylabel("Mean tokens per response")
    ax.set_title("Estimated tokens (95% CI, bootstrap)", fontsize=11, color=TEXT_SECONDARY)

    title = ("More recall with fewer tokens —\nand keyword-only text falls far short" if square
              else "More recall with fewer tokens — and keyword-only text falls far short")
    add_titles(fig, title,
               "recall vs. min_similarity=0.5 + delta=0.1 (limit 8) against the current config. and the two text variants",
               y_title=0.97, y_subtitle=0.84 if square else 0.90)
    add_footer(fig, y=0.015)
    if square:
        fig.subplots_adjust(left=0.11, right=0.96, top=0.74, bottom=0.10, wspace=0.35)
    else:
        fig.subplots_adjust(left=0.07, right=0.97, top=0.78, bottom=0.12, wspace=0.28)
    suffix = "_square" if square else ""
    save_fig(fig, os.path.join(CHARTS_DIR, f"00_hero{suffix}"))


def main():
    os.makedirs(CHARTS_DIR, exist_ok=True)
    raw = load_raw()
    summary = load_summary()
    chart_pareto(summary)
    chart_heatmaps(summary)
    chart_by_type(summary)
    chart_noise(summary)
    chart_hero(raw, square=False)
    chart_hero(raw, square=True)
    print("charts written to", CHARTS_DIR)


if __name__ == "__main__":
    main()
