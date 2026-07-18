# Liberation Serif for Streamlit Community Cloud

## Goal

Make server-generated publication figures deploy reliably on Streamlit Community Cloud while retaining Times New Roman for local Windows exports where it is installed.

## Scope

- Add a root-level `packages.txt` declaring Debian's `fonts-liberation` package.
- Prefer `Times New Roman` whenever it is installed on the runtime host.
- Fall back to `Liberation Serif` when Times New Roman is unavailable, as on Streamlit Community Cloud after its declared Debian package is installed.
- Update the missing-font warning so it explains that neither supported family is available and tells deployers to add the declared Linux package.
- Update focused regression tests for local preference, cloud fallback, and the deployment declaration.

## Non-goals

- Do not add or distribute proprietary font files.
- Do not change Streamlit page UI theme fonts.
- Do not alter chart data, layout, labels, or export formats.

## Deployment flow

After the change is pushed, Streamlit Community Cloud detects `packages.txt`, installs `fonts-liberation` with its Debian dependency step, and restarts the app. The shared policy selects Times New Roman on local Windows hosts where it exists and otherwise selects Liberation Serif; all existing plot builders keep using that same selected family.

## Verification

1. The focused plot-style tests must prove Times New Roman is preferred when available, Liberation Serif is selected when Times New Roman is unavailable, and text artists use the active family.
2. A source-level deployment check must prove the root `packages.txt` declares `fonts-liberation`.
3. Run the focused plot/workflow tests and Python compilation before handoff.
