"""Shared chart style: palette, fonts, footer, save helper."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

BG = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"

PALETTE = {
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "aqua": "#1baf7a",
    "yellow": "#eda100",
    "magenta": "#e87ba4",
    "green": "#008300",
    "violet": "#4a3aa7",
    "red": "#e34948",
}

# Fixed entity -> color assignment, used consistently across every chart.
ENTITY_COLOR = {
    "text_mode": PALETTE["blue"],
    "current_default": PALETTE["orange"],
    "recommended": PALETTE["aqua"],
    "candidate_2": PALETTE["yellow"],
    "other": TEXT_SECONDARY,
    "pareto": PALETTE["violet"],
    "negative_noise": PALETTE["red"],
}

FOOTER = "mcp-brain benchmark · generated locally from the supplied dataset"


def apply_base_style():
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "text.color": TEXT_PRIMARY,
        "axes.edgecolor": GRID,
        "axes.labelcolor": TEXT_PRIMARY,
        "xtick.color": TEXT_SECONDARY,
        "ytick.color": TEXT_SECONDARY,
        "font.size": 11,
        "font.family": "DejaVu Sans",
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.axisbelow": True,
    })


def style_axes(ax):
    ax.set_facecolor(BG)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(True, color=GRID, linewidth=0.7)
    ax.set_axisbelow(True)


def add_titles(fig, title, subtitle, y_title=0.98, y_subtitle=0.90):
    fig.text(0.045, y_title, title, fontsize=15, fontweight="bold", color=TEXT_PRIMARY, ha="left", va="top")
    fig.text(0.045, y_subtitle, subtitle, fontsize=10.5, color=TEXT_SECONDARY, ha="left", va="top")


def add_footer(fig, text=FOOTER, y=0.02):
    fig.text(0.045, y, text, fontsize=8.5, color=TEXT_SECONDARY, ha="left", va="bottom")


def save_fig(fig, path_no_ext):
    fig.savefig(path_no_ext + ".png", dpi=200)
    fig.savefig(path_no_ext + ".svg")
    plt.close(fig)
