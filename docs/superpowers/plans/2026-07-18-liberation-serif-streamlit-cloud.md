# Local Times New Roman with Streamlit Cloud Liberation Serif Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Times New Roman for local chart exports and use Liberation Serif for Streamlit Community Cloud chart exports.

**Architecture:** Keep a single font policy in `src/plot_style.py`; all existing Matplotlib and plotnine call sites already consume that module. The policy selects Times New Roman if present and otherwise selects Liberation Serif; the root Debian dependency ensures the fallback exists on Streamlit Community Cloud.

**Tech Stack:** Python 3, Matplotlib, plotnine, `unittest`, Streamlit Community Cloud Debian dependencies.

## Global Constraints

- Add only the open-source Debian package `fonts-liberation`; do not distribute proprietary font files.
- Prefer Times New Roman on hosts where it is installed and use Liberation Serif only when Times New Roman is unavailable.
- Keep Streamlit UI theme fonts unchanged; affect only server-rendered chart exports.
- Do not change chart data, layout, labels, or export formats.
- Keep PDF and PostScript TrueType embedding settings and the unicode-minus setting unchanged.

---

### Task 1: Deploy Liberation Serif and Select the Best Available Family

**Files:**
- Create: `packages.txt`
- Modify: `src/plot_style.py:8-11`
- Modify: `tests/test_plot_style.py:1-25`

**Interfaces:**
- Consumes: Streamlit Community Cloud's root-level `packages.txt` convention and Debian package name `fonts-liberation`.
- Produces: `select_plot_font() -> str`, returning `Times New Roman` when available and `Liberation Serif` otherwise; `PLOT_FONT_FAMILY` is the selected value; `configure_plot_style() -> list[str]` configures Matplotlib to that family; `packages.txt` contains exactly `fonts-liberation`.

- [ ] **Step 1: Write the failing regression tests**

  Replace the fixed-family test with local-preference and cloud-fallback selection tests, and add the deployment-package test.

  ```python
  from pathlib import Path

  def test_select_plot_font_prefers_times_new_roman_when_available(self):
      self.assertTrue(hasattr(plot_style, "select_plot_font"))
      with patch("src.plot_style.font_available", return_value=True):
          self.assertEqual(plot_style.select_plot_font(), "Times New Roman")

  def test_select_plot_font_uses_liberation_serif_when_times_is_missing(self):
      self.assertTrue(hasattr(plot_style, "select_plot_font"))
      with patch(
          "src.plot_style.font_available",
          side_effect=lambda name: name == "Liberation Serif",
      ):
          self.assertEqual(plot_style.select_plot_font(), "Liberation Serif")

  def test_configure_plot_style_uses_the_active_host_font(self):
      configure_plot_style()
      expected = "Times New Roman" if font_available("Times New Roman") else "Liberation Serif"
      self.assertEqual(PLOT_FONT_FAMILY, expected)
      self.assertEqual(matplotlib.rcParams["font.family"][0], expected)
      self.assertEqual(matplotlib.rcParams["font.serif"][0], expected)
      self.assertEqual(matplotlib.rcParams["pdf.fonttype"], 42)
      self.assertEqual(matplotlib.rcParams["ps.fonttype"], 42)
      self.assertFalse(matplotlib.rcParams["axes.unicode_minus"])

  def test_missing_font_warning_names_both_supported_families(self):
      with patch("src.plot_style.font_available", return_value=False):
          self.assertEqual(
              configure_plot_style(),
              [
                  "Neither Times New Roman nor Liberation Serif is available. "
                  "Install the 'fonts-liberation' package on the runtime host before "
                  "exporting publication figures."
              ],
          )

  def test_streamlit_cloud_package_declaration_installs_liberation_fonts(self):
      package_file = Path(__file__).resolve().parents[1] / "packages.txt"
      self.assertTrue(package_file.is_file())
      self.assertEqual(
          package_file.read_text(encoding="utf-8").splitlines(),
          ["fonts-liberation"],
      )
  ```

- [ ] **Step 2: Run the focused test to verify it fails**

  Run:

  ```powershell
  & 'E:\pyproject\ToxPi_App\.venv\Scripts\python.exe' -m unittest tests.test_plot_style -v
  ```

  Expected: the selector assertions fail because the policy has no runtime choice function, and the active-family assertion fails because the policy is fixed to Liberation Serif.

- [ ] **Step 3: Implement the minimum deployment and font-policy change**

  Create root-level `packages.txt` with exactly:

  ```text
  fonts-liberation
  ```

  Define the family priority and selector in `src/plot_style.py`:

  ```python
  PREFERRED_PLOT_FONT_FAMILY = "Times New Roman"
  FALLBACK_PLOT_FONT_FAMILY = "Liberation Serif"

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
  ```

  Do not change `font_available`, `configure_plot_style`, or `apply_figure_font`; they consume `PLOT_FONT_FAMILY` and therefore apply the selected family to Matplotlib and plotnine output.

- [ ] **Step 4: Run the focused test to verify it passes**

  Run:

  ```powershell
  & 'E:\pyproject\ToxPi_App\.venv\Scripts\python.exe' -m unittest tests.test_plot_style -v
  ```

  Expected: 6 tests pass, proving local Times New Roman preference, Streamlit Cloud Liberation Serif fallback, the active shared configuration, warning, text-artist application, and deployment declaration.

- [ ] **Step 5: Run integration verification**

  Run:

  ```powershell
  & 'E:\pyproject\ToxPi_App\.venv\Scripts\python.exe' -m unittest tests.test_toxpi_plot_text tests.test_use_rose_plot tests.test_cp_screening_workflow tests.test_auto_query_workflow -v
  & 'E:\pyproject\ToxPi_App\.venv\Scripts\python.exe' -m compileall app.py pages src
  ```

  Expected: all selected tests pass and compilation returns exit code 0. These modules prove that the existing standalone and batch chart paths still consume the centralized policy.

- [ ] **Step 6: Review and commit the implementation**

  Run:

  ```powershell
  git -C 'E:\pyproject\ToxPi_App\.worktrees\liberation-serif-streamlit' diff --check
  git -C 'E:\pyproject\ToxPi_App\.worktrees\liberation-serif-streamlit' status --short
  ```

  Stage only `packages.txt`, `src/plot_style.py`, and `tests/test_plot_style.py`, then commit:

  ```powershell
  git -C 'E:\pyproject\ToxPi_App\.worktrees\liberation-serif-streamlit' add packages.txt src/plot_style.py tests/test_plot_style.py
  git -C 'E:\pyproject\ToxPi_App\.worktrees\liberation-serif-streamlit' commit -m "fix: deploy Liberation Serif for chart exports"
  ```
