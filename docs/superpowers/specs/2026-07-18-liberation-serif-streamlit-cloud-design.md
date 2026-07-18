# Liberation Serif for Streamlit Community Cloud

## Goal

Make server-generated publication figures deploy reliably on Streamlit Community Cloud without requiring the proprietary Times New Roman font.

## Scope

- Add a root-level `packages.txt` declaring Debian's `fonts-liberation` package.
- Change the shared Matplotlib/plotnine font family from `Times New Roman` to `Liberation Serif`.
- Update the missing-font warning so it names the required open-source font and tells deployers to add the declared Linux package.
- Update the focused regression test so it asserts the new shared font configuration.

## Non-goals

- Do not add or distribute proprietary font files.
- Do not change Streamlit page UI theme fonts.
- Do not alter chart data, layout, labels, or export formats.

## Deployment flow

After the change is pushed, Streamlit Community Cloud detects `packages.txt`, installs `fonts-liberation` with its Debian dependency step, and restarts the app. `configure_plot_style()` then finds `Liberation Serif`; all existing plot builders keep using the same shared font policy.

## Verification

1. The focused plot-style tests must prove the configured Matplotlib serif family is `Liberation Serif` and that text artists use it.
2. A source-level deployment check must prove the root `packages.txt` declares `fonts-liberation`.
3. Run the focused plot/workflow tests and Python compilation before handoff.
