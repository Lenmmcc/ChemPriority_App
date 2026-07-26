# Streamlit Dataframe Width Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every deprecated Streamlit dataframe `use_container_width=True` argument with the equivalent `width="stretch"` argument.

**Architecture:** This is a source-compatible mechanical migration limited to Streamlit dataframe calls under `pages/`. A source-contract test prevents the deprecated argument from returning, while existing AppTest and unit tests verify that page behavior and scientific workflows remain unchanged.

**Tech Stack:** Python 3, Streamlit 1.58, `unittest`, Streamlit AppTest

## Global Constraints

- Preserve the current full-width dataframe layout.
- Do not change dataframe inputs, column configuration, index visibility, scientific calculations, caching, or exports.
- Do not introduce a compatibility wrapper.
- Do not change non-dataframe Streamlit components.

---

### Task 1: Migrate All Dataframe Width Arguments

**Files:**
- Create: `tests/test_streamlit_dataframe_width_contract.py`
- Modify: `pages/0_综合筛查流程.py`
- Modify: `pages/1_ADMETlab毒性数据获取.py`
- Modify: `pages/2_ToxPi毒性评估.py`
- Modify: `pages/3_EPISuite环境归趋.py`
- Modify: `pages/4_化合物用途查询.py`
- Modify: `pages/6_一键批量查询.py`

**Interfaces:**
- Consumes: Streamlit `st.dataframe(data, width="stretch", ...)`.
- Produces: The same rendered dataframe behavior without the deprecated `use_container_width` keyword.

- [ ] **Step 1: Write the failing source-contract test**

```python
from pathlib import Path
import unittest


class StreamlitDataframeWidthContractTests(unittest.TestCase):
    def test_pages_do_not_use_deprecated_container_width_argument(self):
        offenders = []
        for path in sorted(Path("pages").glob("*.py")):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if "use_container_width" in line:
                    offenders.append(f"{path}:{line_number}")

        self.assertEqual(
            offenders,
            [],
            "Deprecated Streamlit dataframe width arguments remain: "
            + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the RED state**

Run:

```powershell
E:\pyproject\ToxPi_App\.venv\Scripts\python.exe -m unittest tests.test_streamlit_dataframe_width_contract -v
```

Expected: FAIL with `Deprecated Streamlit dataframe width arguments remain` and references to the affected files under `pages/`.

- [ ] **Step 3: Apply the minimal migration**

In every affected `st.dataframe` call, replace:

```python
use_container_width=True
```

with:

```python
width="stretch"
```

Do not alter any other arguments or surrounding code.

- [ ] **Step 4: Run the contract and targeted page tests**

Run:

```powershell
E:\pyproject\ToxPi_App\.venv\Scripts\python.exe -m unittest tests.test_streamlit_dataframe_width_contract tests.test_query_cache.QueryCacheTests.test_page_3_app_renders_cache_diagnostics_from_isolated_cache tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_page_6_accepts_multiple_primary_files_and_keeps_both_in_settings -v
```

Expected: 3 tests run and `OK`, with no `Please replace use_container_width with width` warning.

- [ ] **Step 5: Run complete verification**

Run:

```powershell
E:\pyproject\ToxPi_App\.venv\Scripts\python.exe -m compileall app.py pages src
E:\pyproject\ToxPi_App\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
rg -n "use_container_width" --glob "*.py" .
```

Expected:

- Compilation exits with code 0.
- The full test suite reports `OK`.
- `git diff --check` produces no output.
- The final search produces no matches.

- [ ] **Step 6: Commit the migration**

```powershell
git add -- tests/test_streamlit_dataframe_width_contract.py pages/0_综合筛查流程.py pages/1_ADMETlab毒性数据获取.py pages/2_ToxPi毒性评估.py pages/3_EPISuite环境归趋.py pages/4_化合物用途查询.py pages/6_一键批量查询.py
git commit -m "fix: migrate Streamlit dataframe width arguments"
```
