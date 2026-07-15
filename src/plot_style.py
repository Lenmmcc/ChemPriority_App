from __future__ import annotations

import matplotlib
import matplotlib.font_manager as font_manager
from matplotlib.text import Text


PLOT_FONT_FAMILY = "Times New Roman"
PLOT_FONT_WARNING = (
    "Times New Roman is not available. Install the font on the runtime host "
    "or provide a licensed font file before exporting publication figures."
)


def font_available(name: str) -> bool:
    return any(font.name == name for font in font_manager.fontManager.ttflist)


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
