#!/usr/bin/env python3
"""Charts for Part B (LLM answers) and Part C (agentic), plus Part A fixes."""
import os
import random
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from lib import BENCH
from style import apply_base_style, style_axes, add_titles, add_footer, save_fig, PALETTE, TEXT_SECONDARY, TEXT_PRIMARY, GRID

RESULTS_DIR = os.path.join(BENCH, "results")
CHARTS_DIR = os.path.join(BENCH, "charts")

COND_COLOR = {
    "texto_keywords": PALETTE["blue"],
    "semantica_actual": PALETTE["orange"],
    "semantica_recomendada": PALETTE["aqua"],
    "repo_sin_memoria": PALETTE["red"],
    "sin_memoria": PALETTE["red"],
    "oraculo": "#4a3aa7",
    "solo_brain": PALETTE["yellow"],
    "sin_nada": TEXT_SECONDARY,
    "repo_con_brain": PALETTE["aqua"],
}
COND_LABEL = {
    "texto_keywords": "Text (keywords)",
    "semantica_actual": "Semantic (current config)",
    "semantica_recomendada": "Semantic (recommended)",
    "sin_memoria": "No memory",
    "repo_sin_memoria": "Repo, no memory",
    "oraculo": "Oracle",
    "solo_brain": "Brain only",
    "sin_nada": "Nothing (floor)",
    "repo_con_brain": "Repo + brain MCP",
}


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
    return (sum(vals) / n, lo, hi)


def grouped_bar(ax, groups, cats, data_fn, color_fn, label_fn, ci=True, value_fmt="{:.0%}"):
    """groups: outer x categories (e.g. models); cats: bars within each group (conditions)."""
    n_groups = len(groups)
    n_cats = len(cats)
    width = 0.8 / n_cats
    x = np.arange(n_groups)
    for j, cat in enumerate(cats):
        means, los, his = [], [], []
        for g in groups:
            vals = data_fn(g, cat)
            m, lo, hi = bootstrap_ci(vals) if ci else (np.mean(vals), None, None)
            means.append(m)
            los.append(lo)
            his.append(hi)
        xs = x + (j - (n_cats - 1) / 2) * width
        err = None
        if ci:
            err = [[m - lo for m, lo in zip(means, los)], [hi - m for m, hi in zip(means, his)]]
        ax.bar(xs, means, width=width * 0.92, color=color_fn(cat), label=label_fn(cat),
               yerr=err if ci else None, capsize=3, ecolor=TEXT_SECONDARY, error_kw={"linewidth": 1})
        for i, (xi, m) in enumerate(zip(xs, means)):
            label_y = (his[i] if ci and his[i] is not None else m) + 0.02
            ax.text(xi, label_y, value_fmt.format(m), ha="center", va="bottom", fontsize=8.5, color=TEXT_PRIMARY)
    ax.set_xticks(x)
    ax.set_xticklabels(groups)


# ================= PART C =================

def load_c():
    return pd.read_csv(os.path.join(RESULTS_DIR, "agent_raw.csv"))


def c_hero(df):
    apply_base_style()
    models = ["haiku", "sonnet"]
    conds = ["repo_sin_memoria", "repo_con_brain", "solo_brain", "sin_nada"]
    fig, ax = plt.subplots(figsize=(9, 9))
    grouped_bar(ax, models, conds,
                lambda g, c: df[(df.model == g) & (df.condition == c)].score.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c])
    style_axes(ax)
    ax.set_ylim(0, 1.3)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_ylabel("Mean accuracy (score 0-1)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.17), frameon=False, fontsize=8.5, ncol=4)
    add_titles(fig, "The brain MCP raises agent accuracy and levels it across models",
               "Mean accuracy by model and condition")
    add_footer(fig, "Generated locally from the supplied agent dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.78, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "c_00_hero"))


def c_por_tipo(df):
    apply_base_style()
    conds = ["repo_sin_memoria", "repo_con_brain", "solo_brain"]
    types = ["codigo", "decision", "negativa"]
    type_label = {"codigo": "Code", "decision": "Decision", "negativa": "Negative"}
    fig, ax = plt.subplots(figsize=(12, 6.75))
    grouped_bar(ax, [type_label[t] for t in types], conds,
                lambda g, c: df[(df.question_type == types[[type_label[t] for t in types].index(g)]) & (df.condition == c)].score.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c])
    style_axes(ax)
    ax.set_ylim(0, 1.2)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_ylabel("Mean accuracy (score 0-1)")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    add_titles(fig, "Accuracy by question type and condition",
               "Models combined; inspect the supplied dataset for question counts")
    add_footer(fig, "Generated locally from the supplied agent dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "c_01_por_tipo"))


def c_tiempo_tokens(df):
    apply_base_style()
    models = ["haiku", "sonnet"]
    conds = ["repo_sin_memoria", "repo_con_brain", "solo_brain"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.75))

    ax = axes[0]
    width = 0.8 / len(conds)
    x = np.arange(len(models))
    for j, c in enumerate(conds):
        vals = [df[(df.model == m) & (df.condition == c)].duration_ms.median() / 1000 for m in models]
        xs = x + (j - 1) * width
        ax.bar(xs, vals, width=width * 0.92, color=COND_COLOR[c], label=COND_LABEL[c])
        for xi, v in zip(xs, vals):
            ax.text(xi, v + 0.15, f"{v:.1f}s", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(x); ax.set_xticklabels(models)
    style_axes(ax)
    ax.set_ylabel("Median duration (s)")
    ax.set_title("Response time", fontsize=11, loc="left", color=TEXT_PRIMARY)

    ax = axes[1]
    for j, c in enumerate(conds):
        vals = [df[(df.model == m) & (df.condition == c)].total_tokens.mean() for m in models]
        xs = x + (j - 1) * width
        ax.bar(xs, vals, width=width * 0.92, color=COND_COLOR[c], label=COND_LABEL[c])
        for xi, v in zip(xs, vals):
            ax.text(xi, v + 300, f"{v/1000:.1f}k", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(x); ax.set_xticklabels(models)
    style_axes(ax)
    ax.set_ylabel("Mean total tokens")
    ax.set_title("Token usage", fontsize=11, loc="left", color=TEXT_PRIMARY)
    ax.legend(loc="upper right", frameon=False, fontsize=8.5)

    add_titles(fig, "The brain MCP also responds faster and with fewer tokens",
               "Median duration and mean total tokens by model and condition")
    add_footer(fig, "Duration and token summaries from the supplied agent dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10, wspace=0.28)
    save_fig(fig, os.path.join(CHARTS_DIR, "c_02_tiempo_tokens"))


def c_alucinaciones(df):
    apply_base_style()
    models = ["haiku", "sonnet"]
    conds = ["repo_sin_memoria", "repo_con_brain", "solo_brain"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.75))

    ax = axes[0]
    grouped_bar(ax, models, conds,
                lambda g, c: df[(df.model == g) & (df.condition == c)].hallucination.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c])
    style_axes(ax)
    ax.set_ylim(0, 0.35)
    ax.set_ylabel("Hallucination rate")
    ax.set_title("All questions", fontsize=11, loc="left", color=TEXT_PRIMARY)
    ax.legend(loc="upper right", frameon=False, fontsize=8)

    ax = axes[1]
    dfn = df[df.question_type == "negativa"]
    grouped_bar(ax, models, conds,
                lambda g, c: dfn[(dfn.model == g) & (dfn.condition == c)].hallucination.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c])
    style_axes(ax)
    ax.set_ylim(0, 0.35)
    ax.set_ylabel("Hallucination rate")
    ax.set_title("'Negative' questions only (no answer in repo)", fontsize=11, loc="left", color=TEXT_PRIMARY)

    add_titles(fig, "Without memory, the agent invents answers twice as often",
               "Hallucination rate by model and condition, overall and on unanswerable questions")
    add_footer(fig, "Negative-question subset from the supplied agent dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10, wspace=0.3)
    save_fig(fig, os.path.join(CHARTS_DIR, "c_03_alucinaciones"))


def c_coste(df):
    apply_base_style()
    models = ["haiku", "sonnet"]
    conds = ["repo_sin_memoria", "repo_con_brain", "solo_brain"]
    fig, ax = plt.subplots(figsize=(12, 6.75))
    width = 0.8 / len(conds)
    x = np.arange(len(models))
    for j, c in enumerate(conds):
        vals = []
        for m in models:
            sub = df[(df.model == m) & (df.condition == c)]
            vals.append(sub.total_cost_usd.mean() / sub.score.mean())
        xs = x + (j - 1) * width
        ax.bar(xs, vals, width=width * 0.92, color=COND_COLOR[c], label=COND_LABEL[c])
        for xi, v in zip(xs, vals):
            ax.text(xi, v + 0.0005, f"${v:.3f}", ha="center", va="bottom", fontsize=8.5)
    ax.set_xticks(x); ax.set_xticklabels(models)
    style_axes(ax)
    ax.set_ylabel("Equivalent API cost per correct answer (USD)")
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    add_titles(fig, "Every correct answer costs less with memory",
               "Equivalent API cost (total_cost_usd / accuracy) by model and condition — not a billing statement")
    add_footer(fig, "Equivalent cost estimated from configured model pricing · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "c_04_coste_por_acierto"))


# ================= PART B =================

def load_b():
    return pd.read_csv(os.path.join(RESULTS_DIR, "llm_raw.csv"))


def b_hero(df):
    apply_base_style()
    models = ["haiku", "sonnet", "opus"]
    conds = ["sin_memoria", "texto_keywords", "semantica_actual", "semantica_recomendada", "oraculo"]
    fig, ax = plt.subplots(figsize=(9, 9))
    def dfn(g, c):
        return df[(df.model_alias == g) & (df.condition == c)].score.values
    # opus has no texto_keywords/oraculo rows
    n_groups = len(models); n_cats = len(conds)
    width = 0.8 / n_cats
    x = np.arange(n_groups)
    for j, c in enumerate(conds):
        means, los, his, xs2 = [], [], [], []
        for i, g in enumerate(models):
            vals = dfn(g, c)
            if len(vals) == 0:
                continue
            m, lo, hi = bootstrap_ci(vals)
            means.append(m); los.append(lo); his.append(hi)
            xs2.append(x[i] + (j - (n_cats - 1) / 2) * width)
        if not means:
            continue
        err = [[m - lo for m, lo in zip(means, los)], [hi - m for m, hi in zip(means, his)]]
        ax.bar(xs2, means, width=width * 0.92, color=COND_COLOR[c], label=COND_LABEL[c],
               yerr=err, capsize=3, ecolor=TEXT_SECONDARY, error_kw={"linewidth": 1})
        # stagger label height by condition index to avoid horizontal collisions
        # when several bars in a group sit at nearly the same value (~0.95-1.00)
        stagger = 0.045 * (j % 2)
        for xi, m, hi in zip(xs2, means, his):
            ax.text(xi, hi + 0.02 + stagger, f"{m:.0%}", ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(x); ax.set_xticklabels(models)
    style_axes(ax)
    ax.set_ylim(0, 1.35)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_ylabel("Mean accuracy (score 0-1)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.20), frameon=False, fontsize=7.5, ncol=2)
    add_titles(fig, "No memory scores 1 of 7; with semantic\nmemory, 49 of 50",
               "Answer accuracy by model and condition")
    add_footer(fig, "Generated locally from the supplied dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.66, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "b_00_hero"))


def b_acierto_tokens(df):
    apply_base_style()
    fig, ax = plt.subplots(figsize=(12, 6.75))
    conds = ["sin_memoria", "texto_keywords", "semantica_actual", "semantica_recomendada", "oraculo"]
    models = ["haiku", "sonnet", "opus"]
    marker = {"haiku": "o", "sonnet": "s", "opus": "^"}
    for m in models:
        for c in conds:
            sub = df[(df.model_alias == m) & (df.condition == c)]
            if len(sub) == 0:
                continue
            acc = sub.score.mean()
            tok = sub.input_tokens_total.mean()
            ax.scatter(tok, acc, color=COND_COLOR[c], marker=marker[m], s=110, edgecolor="white", linewidth=0.8, zorder=3)
            ax.annotate(f"{m}", (tok, acc), textcoords="offset points", xytext=(6, 4), fontsize=7.5, color=TEXT_SECONDARY)
    style_axes(ax)
    ax.set_xlabel("Mean input tokens")
    ax.set_ylabel("Mean accuracy (score 0-1)")
    ax.set_ylim(0, 1.1)
    handles = [plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=COND_COLOR[c], markersize=9, label=COND_LABEL[c]) for c in conds]
    ax.legend(handles=handles, loc="lower right", frameon=False, fontsize=8.5)
    add_titles(fig, "More context tokens is not what explains accuracy",
               "Accuracy vs mean input tokens by model and condition")
    add_footer(fig, "Input-token summary from the supplied dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "b_01_acierto_vs_tokens"))


def b_alucinaciones_negativas(df):
    apply_base_style()
    models = ["haiku", "sonnet"]
    conds = ["sin_memoria", "texto_keywords", "semantica_actual", "semantica_recomendada"]
    dfn = df[df.query_type == "negative"]
    fig, ax = plt.subplots(figsize=(12, 6.75))
    grouped_bar(ax, models, conds,
                lambda g, c: dfn[(dfn.model_alias == g) & (dfn.condition == c)].hallucination.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c], ci=False)
    style_axes(ax)
    ax.set_ylim(0, 0.3)
    ax.set_ylabel("Hallucination rate (negative questions)")
    ax.legend(loc="upper right", frameon=False, fontsize=8.5)
    add_titles(fig, "Hallucination rate on unanswerable questions",
               "Negative-question subset by model and condition")
    add_footer(fig, "Generated locally from the supplied dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "b_02_alucinaciones_negativas"))


def b_por_tipo(df):
    apply_base_style()
    conds = ["sin_memoria", "texto_keywords", "semantica_actual", "semantica_recomendada", "oraculo"]
    types = ["paraphrase", "keyword", "multi", "negative"]
    type_label = {"paraphrase": "Paraphrase", "keyword": "Keyword", "multi": "Multi-fact", "negative": "Negative"}
    sub = df[df.model_alias == "sonnet"]
    fig, ax = plt.subplots(figsize=(12, 6.75))
    grouped_bar(ax, [type_label[t] for t in types], conds,
                lambda g, c: sub[(sub.query_type == types[[type_label[t] for t in types].index(g)]) & (sub.condition == c)].score.values,
                lambda c: COND_COLOR[c], lambda c: COND_LABEL[c], ci=False)
    style_axes(ax)
    ax.set_ylim(0, 1.3)
    ax.set_yticks(np.arange(0, 1.01, 0.2))
    ax.set_ylabel("Mean accuracy (score 0-1)")
    ax.legend(loc="upper right", frameon=False, fontsize=7.5, ncol=2)
    add_titles(fig, "Accuracy by query type and condition",
               "Sonnet model, using the supplied dataset")
    add_footer(fig, "Generated locally from the supplied dataset · mcp-brain benchmark")
    fig.subplots_adjust(top=0.82, bottom=0.10)
    save_fig(fig, os.path.join(CHARTS_DIR, "b_03_por_tipo"))


if __name__ == "__main__":
    dfc = load_c()
    c_hero(dfc)
    c_por_tipo(dfc)
    c_tiempo_tokens(dfc)
    c_alucinaciones(dfc)
    c_coste(dfc)

    dfb = load_b()
    b_hero(dfb)
    b_acierto_tokens(dfb)
    b_alucinaciones_negativas(dfb)
    b_por_tipo(dfb)

    print("done")
