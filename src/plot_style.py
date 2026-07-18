from __future__ import annotations

import matplotlib
import matplotlib.font_manager as font_manager
from matplotlib.text import Text


PREFERRED_PLOT_FONT_FAMILY = "Times New Roman"
FALLBACK_PLOT_FONT_FAMILY = "Liberation Serif"


def font_available(name: str) -> bool:
    return any(font.name == name for font in font_manager.fontManager.ttflist)


def select_plot_font() -> str:
    if font_available(PREFERRED_PLOT_FONT_FAMILY):
        return PREFERRED_PLOT_FONT_FAMILY
    return FALLBACK_PLOT_FONT_FAMILY


PLOT_FONT_FAMILY = select_plot_font()
PLOT_FONT_WARNING = (
    "Neither Times New Roman nor Liberation Serif is available. "
    "Install the 'fonts-liberation' package on the runtime host before "
    "exporting publication figures."
)


def configure_plot_style() -> list[str]:
    matplotlib.rcParams.update(
        {
            "font.family": [PLOT_FONT_FAMILY],
            "font.serif": [PLOT_FONT_FAMILY],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    return [] if font_available(PLOT_FONT_FAMILY) else [PLOT_FONT_WARNING]


def apply_figure_font(fig):
    for text in fig.findobj(Text):
        text.set_fontfamily(PLOT_FONT_FAMILY)
    return fig
