# EPI Suite Properties Column Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify the public EPI Suite Properties schema and expand the page indicator descriptions without changing the core fate endpoint contract.

**Architecture:** Keep KOAWIN raw normalization under the internal `koawin_*` names, then expose only `log_kaw` in the public enrichment returned to the Properties row. Build the Properties row in explicit semantic stages so its insertion order is the shared page and Excel contract. Add a display-only `TARGET_INDICATOR_DESCRIPTIONS` list so explanatory rows do not expand `ENDPOINT_KEYS`.

**Tech Stack:** Python 3, pandas, Streamlit, RDKit, openpyxl, `unittest`.

## Global Constraints

- Delete public `log_koa_selected`; retain `log_koa_estimated`, `log_koa_experimental`, `log_koa_type`, and `log_koa_units`.
- Rename public `koawin_log_kaw` to `log_kaw`; retain internal KOAWIN extraction and validation names.
- The exact adjacent order is `log_koa_estimated`, `log_koa_experimental`, `log_koa_type`, `log_koa_units`, `log_kaw`, `tpsa_rdkit_a2`, `mr_rdkit_cm3_mol`, `melting_point_selected`.
- Page tables and Excel `Properties` use the same DataFrame names and order.
- Do not add logKoa, logKaw, TPSA, or MR to `ENDPOINT_KEYS`.
- Use the approved Chinese descriptions verbatim, including the logKoc and logKoa fallback rules.
- Do not change numerical calculations, KOAWIN warnings, Raw API JSON, or the desktop installer.

---

### Task 1: Separate Page Indicator Descriptions from the Core Endpoint Contract

**Files:**
- Modify: `tests/test_episuite_display.py`
- Modify: `src/episuite_io.py:29-123`
- Modify: `pages/3_EPISuite环境归趋.py:17-22,227-228`

**Interfaces:**
- Consumes: existing `FATE_ENDPOINTS: list[dict[str, str]]` and `ENDPOINT_KEYS: list[str]`.
- Produces: `TARGET_INDICATOR_DESCRIPTIONS: list[dict[str, str]]`, used only by the page explanation table.

- [ ] **Step 1: Write failing metadata contract tests**

Add imports and tests that assert the exact approved descriptions, the extra display rows, and the unchanged core key set:

```python
from src.episuite_io import (
    ENDPOINT_KEYS,
    FATE_ENDPOINTS,
    TARGET_INDICATOR_DESCRIPTIONS,
)

def test_target_indicator_descriptions_include_partition_and_rdkit_context(self):
    descriptions = {
        item["endpoint"]: (item["model"], item["description"])
        for item in TARGET_INDICATOR_DESCRIPTIONS
    }
    self.assertEqual(
        descriptions["log_kow"],
        (
            "KOWWIN",
            "辛醇/水分配系数 logKow（优先采用实验值；无实验值时采用 KOWWIN 估算值）",
        ),
    )
    self.assertEqual(
        descriptions["log_koa"],
        (
            "KOAWIN",
            "辛醇/空气分配系数 logKoa（优先采用实验值；无实验值时采用 KOAWIN 估算值）",
        ),
    )
    self.assertEqual(
        descriptions["log_kaw"],
        ("KOAWIN", "空气/水分配系数 logKaw（由 KOAWIN 的 KAW 取 log10）"),
    )
    self.assertEqual(
        descriptions["tpsa_rdkit_a2"],
        ("RDKit", "拓扑极性表面积 TPSA（Å²；RDKit 结构计算值）"),
    )
    self.assertEqual(
        descriptions["mr_rdkit_cm3_mol"],
        ("RDKit", "Wildman–Crippen 摩尔折射率 MR（cm³/mol；RDKit 结构计算值）"),
    )
    self.assertEqual(
        descriptions["log_koc"],
        (
            "KOCWIN",
            "有机碳归一化吸附系数 logKoc（优先采用实验值；无实验值时采用 KOCWIN 的 MCI 估算值）",
        ),
    )

def test_display_only_indicators_do_not_expand_core_endpoint_keys(self):
    self.assertEqual(ENDPOINT_KEYS, [item["endpoint"] for item in FATE_ENDPOINTS])
    for endpoint in ("log_koa", "log_kaw", "tpsa_rdkit_a2", "mr_rdkit_cm3_mol"):
        self.assertNotIn(endpoint, ENDPOINT_KEYS)

def test_page_uses_target_indicator_descriptions(self):
    page_source = Path("pages/3_EPISuite环境归趋.py").read_text(encoding="utf-8")
    self.assertIn("TARGET_INDICATOR_DESCRIPTIONS", page_source)
    self.assertIn("pd.DataFrame(TARGET_INDICATOR_DESCRIPTIONS)", page_source)
    self.assertNotIn("pd.DataFrame(FATE_ENDPOINTS)", page_source)
```

- [ ] **Step 2: Run the focused display tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display -v`

Expected: FAIL because `TARGET_INDICATOR_DESCRIPTIONS` is not defined and the page still renders `FATE_ENDPOINTS`.

- [ ] **Step 3: Implement the display-only description list**

Update the approved core descriptions and define the new list after `ENDPOINT_KEYS`:

```python
ENDPOINT_KEYS = [item["endpoint"] for item in FATE_ENDPOINTS]

TARGET_INDICATOR_DESCRIPTIONS = [
    FATE_ENDPOINTS[0],
    {
        "endpoint": "log_koa",
        "model": "KOAWIN",
        "description": "辛醇/空气分配系数 logKoa（优先采用实验值；无实验值时采用 KOAWIN 估算值）",
    },
    {
        "endpoint": "log_kaw",
        "model": "KOAWIN",
        "description": "空气/水分配系数 logKaw（由 KOAWIN 的 KAW 取 log10）",
    },
    {
        "endpoint": "tpsa_rdkit_a2",
        "model": "RDKit",
        "description": "拓扑极性表面积 TPSA（Å²；RDKit 结构计算值）",
    },
    {
        "endpoint": "mr_rdkit_cm3_mol",
        "model": "RDKit",
        "description": "Wildman–Crippen 摩尔折射率 MR（cm³/mol；RDKit 结构计算值）",
    },
    *FATE_ENDPOINTS[1:],
]
```

Change the page import and rendering call from `FATE_ENDPOINTS` to `TARGET_INDICATOR_DESCRIPTIONS`.

- [ ] **Step 4: Run the focused display tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display -v`

Expected: all tests in `tests.test_episuite_display` pass.

- [ ] **Step 5: Commit the page indicator contract**

```powershell
git add src/episuite_io.py pages/3_EPISuite环境归趋.py tests/test_episuite_display.py
git commit -m "feat: expand EPI indicator descriptions"
```

### Task 2: Enforce the Public Properties Column Name and Order

**Files:**
- Modify: `tests/test_episuite_properties.py`
- Modify: `tests/test_episuite_cas_values.py:668-715,850-900`
- Modify: `src/episuite_properties.py:15-21,178-183`
- Modify: `src/episuite_io.py:1030-1075`

**Interfaces:**
- Consumes: `extract_koawin_partition_fields(data) -> tuple[dict, list[str]]` with internal `koawin_log_kaw` unchanged.
- Produces: `build_epi_property_enrichment(...) -> tuple[dict, list[str]]` with public keys `log_kaw`, `tpsa_rdkit_a2`, and `mr_rdkit_cm3_mol`; `_build_properties_row(...)` returns the approved ordered public mapping.

- [ ] **Step 1: Write failing public enrichment and DataFrame contract tests**

Update public enrichment expectations to `log_kaw`, while retaining internal extractor assertions for `koawin_log_kaw`. Replace the Properties table assertions with:

```python
expected_adjacent_columns = [
    "log_koa_estimated",
    "log_koa_experimental",
    "log_koa_type",
    "log_koa_units",
    "log_kaw",
    "tpsa_rdkit_a2",
    "mr_rdkit_cm3_mol",
    "melting_point_selected",
]
start = properties.columns.get_loc("log_koa_estimated")
self.assertEqual(
    properties.columns[start : start + len(expected_adjacent_columns)].tolist(),
    expected_adjacent_columns,
)
self.assertNotIn("log_koa_selected", properties.columns)
self.assertNotIn("koawin_log_kaw", properties.columns)
self.assertAlmostEqual(
    properties.loc[0, "log_kaw"],
    math.log10(response["logKoa"]["estimatedValue"]["model"]["kaw"]),
)
```

Update the workbook assertion so `list(rows[0]) == list(properties.columns)` and the numeric checks use `log_kaw`.

- [ ] **Step 2: Run focused Properties tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_cas_values -v`

Expected: FAIL because public enrichment still emits `koawin_log_kaw`, `log_koa_selected` is present, and enrichment columns are still at the end.

- [ ] **Step 3: Rename only the public enrichment field**

Keep `PARTITION_COLUMN_ORDER` and `extract_koawin_partition_fields` unchanged. Change only the public order and mapping:

```python
PUBLIC_EPI_ENRICHMENT_ORDER = (
    "log_kaw",
    "tpsa_rdkit_a2",
    "mr_rdkit_cm3_mol",
)

public_fields = {
    "log_kaw": partition_fields["koawin_log_kaw"],
    **descriptor_fields,
}
```

- [ ] **Step 4: Build the Properties row in explicit stages**

In `_build_properties_row`, add logKow and logKoa first, remove only `log_koa_selected`, insert enrichment, and then append melting point and the remaining sections:

```python
for prefix, section in [("log_kow", "logKow"), ("log_koa", "logKoa")]:
    row.update(_selected_estimated_experimental_columns(prefix, data, section))
row.pop("log_koa_selected", None)

enrichment, warnings = build_epi_property_enrichment(
    data,
    epi_smiles=base.get("epi_smiles"),
    input_smiles=base.get("smiles"),
)
row.update(enrichment)

for prefix, section in [
    ("melting_point", "meltingPoint"),
    ("boiling_point", "boilingPoint"),
    ("vapor_pressure", "vaporPressure"),
    ("henry", "henrysLawConstant"),
    ("log_koc", "logKoc"),
    ("aerosol_adsorption_fraction", "aerosolAdsorptionFraction"),
]:
    row.update(_selected_estimated_experimental_columns(prefix, data, section))
```

Leave the water-solubility and dermal fields after this loop, and return the earlier `warnings` variable.

- [ ] **Step 5: Run focused Properties tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_cas_values -v`

Expected: all focused Properties and EPI structured-result tests pass.

- [ ] **Step 6: Commit the Properties contract**

```powershell
git add src/episuite_properties.py src/episuite_io.py tests/test_episuite_properties.py tests/test_episuite_cas_values.py
git commit -m "refactor: unify EPI Properties column contract"
```

### Task 3: Verify Page, Export, and Repository Regressions

**Files:**
- Verify: `pages/3_EPISuite环境归趋.py`
- Verify: `src/episuite_io.py`
- Verify: `src/episuite_properties.py`
- Verify: `tests/test_episuite_display.py`
- Verify: `tests/test_episuite_properties.py`
- Verify: `tests/test_episuite_cas_values.py`

**Interfaces:**
- Consumes: final `TARGET_INDICATOR_DESCRIPTIONS` and Properties DataFrame contract.
- Produces: fresh evidence that focused tests, all tests, compilation, and repository hygiene pass.

- [ ] **Step 1: Run all focused EPI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display tests.test_episuite_properties tests.test_episuite_cas_values tests.test_episuite_result_pool tests.test_episuite_supplement -v
```

Expected: exit code 0 with no failures or errors.

- [ ] **Step 2: Run the complete test suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: exit code 0 with all discovered tests passing.

- [ ] **Step 3: Compile application modules**

Run: `.\.venv\Scripts\python.exe -m compileall app.py pages src`

Expected: exit code 0 with no syntax errors.

- [ ] **Step 4: Check the final diff for whitespace and scope**

Run: `git diff --check`

Expected: no output and exit code 0.

Run: `git status --short --branch`

Expected: only the approved EPI files and documentation are changed or ahead of `origin/main`; pre-existing unrelated untracked files remain untouched.

- [ ] **Step 5: Review requirements against the design**

Confirm all of the following from the final diff and test evidence:

```text
log_koa_selected absent from public Properties
koawin_log_kaw absent from public Properties
log_kaw value preserved
approved eight-column adjacency preserved
Excel headers exactly match Properties columns
approved indicator descriptions present
ENDPOINT_KEYS unchanged in scope
internal KOAWIN warnings and raw JSON preserved
```
