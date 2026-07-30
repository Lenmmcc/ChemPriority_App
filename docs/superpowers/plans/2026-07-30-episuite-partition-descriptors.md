# EPI Suite Partition Coefficients and RDKit Descriptors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add paired log10/raw KOW, KOA, and KAW values plus RDKit TPSA/MR to the third-page EPI Suite `Properties` results and Excel export without changing existing EPI model semantics.

**Architecture:** Put KOAWIN normalization and RDKit calculations in a focused `src/episuite_properties.py` module. `src/episuite_io.py` will call that module while building the existing `Properties` DataFrame and merge non-fatal enrichment warnings into the existing `Warnings` table. A separate display helper will supply Streamlit labels and numeric formats without converting stored numbers to strings.

**Tech Stack:** Python 3, pandas, RDKit, Streamlit, openpyxl, unittest

## Global Constraints

- Add exactly these visible fields: `koawin_log_kow`, `koawin_kow`, `koawin_log_koa`, `koawin_koa`, `koawin_log_kaw`, `koawin_kaw`, `tpsa_rdkit_a2`, and `mr_rdkit_cm3_mol`.
- Add the eight fields only to `Properties / 理化性质`; do not duplicate them in `Core_Summary` or create a new worksheet.
- Preserve all existing selected / estimated / experimental EPI fields and `EPI_REPORT_HIDDEN_COMPAT_COLUMNS`.
- Preserve API coefficient floats and Excel numeric cell types; UI formatting must not mutate the DataFrame.
- Use API `chemicalProperties.smiles`, then saved `epi_smiles`, then input `smiles` for RDKit calculations.
- Invalid or missing SMILES leaves TPSA/MR empty and adds a warning without failing the EPI result.
- Do not add, query, estimate, or reserve a column for `ΔHvap`.
- Do not add an external API, dependency, cache key, checkpoint field, or request parameter.

## File Structure

- Create `src/episuite_properties.py`: normalize KOAWIN coefficients, calculate paired log10 values, validate coefficient relationships, select SMILES, and calculate RDKit TPSA/MR.
- Create `src/episuite_display.py`: define shared page/Excel labels, Streamlit numeric formats, and a non-mutating Excel export view for the eight fields.
- Create `tests/test_episuite_properties.py`: focused unit tests for coefficient normalization, warnings, SMILES priority, and RDKit failure behavior.
- Create `tests/test_episuite_display.py`: prove labels/formats are generated without mutating numeric data and prove page 3 uses the helper.
- Modify `src/episuite_io.py:896-928,1005-1039`: integrate enrichment fields and warnings into categorized result tables.
- Modify `tests/test_episuite_cas_values.py:600-657`: verify `Properties`, `Warnings`, `Core_Summary`, raw JSON, and workbook output together.
- Modify `pages/3_EPISuite环境归趋.py:1-30,147-159`: apply labels and display formats only to the `Properties` tab.

---

### Task 1: Normalize the KOAWIN coefficient set

**Files:**
- Create: `src/episuite_properties.py`
- Create: `tests/test_episuite_properties.py`

**Interfaces:**
- Produces: `extract_koawin_partition_fields(data: dict) -> tuple[dict[str, float | None], list[str]]`
- Produces columns in this order: `koawin_log_kow`, `koawin_kow`, `koawin_log_koa`, `koawin_koa`, `koawin_log_kaw`, `koawin_kaw`
- Later tasks consume the returned field dictionary and warning strings.

- [ ] **Step 1: Write failing tests for direct extraction, log pairing, fallback, and inconsistency warnings**

Create `tests/test_episuite_properties.py`:

```python
import math
import unittest

from src.episuite_properties import extract_koawin_partition_fields


class EPISuitePropertyEnrichmentTests(unittest.TestCase):
    def test_extracts_api_coefficients_and_builds_paired_log10_values(self):
        data = {
            "logKow": {"estimatedValue": {"value": 3.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 5.0,
                    "model": {
                        "kow": 1000.0,
                        "kaw": 0.01,
                        "koa": 100000.0,
                        "logKoa": 5.0,
                    },
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertEqual(
            tuple(fields),
            (
                "koawin_log_kow",
                "koawin_kow",
                "koawin_log_koa",
                "koawin_koa",
                "koawin_log_kaw",
                "koawin_kaw",
            ),
        )
        self.assertEqual(fields["koawin_kow"], 1000.0)
        self.assertEqual(fields["koawin_koa"], 100000.0)
        self.assertEqual(fields["koawin_kaw"], 0.01)
        self.assertEqual(fields["koawin_log_kow"], 3.0)
        self.assertEqual(fields["koawin_log_koa"], 5.0)
        self.assertEqual(fields["koawin_log_kaw"], -2.0)
        self.assertEqual(warnings, [])

    def test_recovers_missing_coefficients_from_available_model_logs(self):
        data = {
            "logKow": {"estimatedValue": {"value": 2.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 6.0,
                    "model": {"logKoa": 6.0},
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertTrue(math.isclose(fields["koawin_kow"], 100.0))
        self.assertTrue(math.isclose(fields["koawin_koa"], 1000000.0))
        self.assertIsNone(fields["koawin_kaw"])
        self.assertIsNone(fields["koawin_log_kaw"])
        self.assertEqual(warnings, [])

    def test_preserves_inconsistent_api_coefficients_and_warns(self):
        data = {
            "logKow": {"estimatedValue": {"value": 2.0}},
            "logKoa": {
                "estimatedValue": {
                    "value": 4.0,
                    "model": {
                        "kow": 100.0,
                        "kaw": 0.1,
                        "koa": 5000.0,
                        "logKoa": 4.0,
                    },
                }
            },
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertEqual(fields["koawin_koa"], 5000.0)
        self.assertEqual(fields["koawin_log_koa"], math.log10(5000.0))
        self.assertIn("KOAWIN 原始系数关系不一致：KOA != KOW / KAW", warnings)
        self.assertIn("KOAWIN logKOA 与 KOA 不一致", warnings)

    def test_nonpositive_or_nonfinite_coefficients_stay_missing(self):
        data = {
            "logKoa": {
                "estimatedValue": {
                    "model": {
                        "kow": 0.0,
                        "kaw": float("inf"),
                        "koa": "not-a-number",
                    }
                }
            }
        }

        fields, warnings = extract_koawin_partition_fields(data)

        self.assertTrue(all(value is None for value in fields.values()))
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify the module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.episuite_properties'`.

- [ ] **Step 3: Implement the minimal KOAWIN normalizer**

Create `src/episuite_properties.py`:

```python
from __future__ import annotations

import math


PARTITION_COLUMN_ORDER = (
    "koawin_log_kow",
    "koawin_kow",
    "koawin_log_koa",
    "koawin_koa",
    "koawin_log_kaw",
    "koawin_kaw",
)
COEFFICIENT_REL_TOL = 1e-9
LOG_ABS_TOL = 1e-9


def _nested_value(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _finite_float(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_float(value):
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _coefficient_and_log(coefficient, direct_log):
    coefficient_value = _positive_float(coefficient)
    log_value = _finite_float(direct_log)
    if coefficient_value is None and log_value is not None:
        try:
            coefficient_value = 10.0 ** log_value
        except OverflowError:
            coefficient_value = None
    if coefficient_value is None or not math.isfinite(coefficient_value):
        return None, None
    return coefficient_value, math.log10(coefficient_value)


def extract_koawin_partition_fields(data: dict):
    model = _nested_value(data, "logKoa", "estimatedValue", "model")
    if not isinstance(model, dict):
        model = {}

    direct_logs = {
        "kow": _nested_value(data, "logKow", "estimatedValue", "value"),
        "koa": model.get("logKoa")
        if model.get("logKoa") is not None
        else _nested_value(data, "logKoa", "estimatedValue", "value"),
        "kaw": None,
    }
    coefficients = {}
    paired_logs = {}
    for name in ("kow", "koa", "kaw"):
        coefficients[name], paired_logs[name] = _coefficient_and_log(
            model.get(name),
            direct_logs[name],
        )

    warnings = []
    if all(coefficients[name] is not None for name in ("kow", "koa", "kaw")):
        expected_koa = coefficients["kow"] / coefficients["kaw"]
        if not math.isclose(
            coefficients["koa"],
            expected_koa,
            rel_tol=COEFFICIENT_REL_TOL,
            abs_tol=0.0,
        ):
            warnings.append("KOAWIN 原始系数关系不一致：KOA != KOW / KAW")

    for name, label in (("kow", "KOW"), ("koa", "KOA")):
        direct_log = _finite_float(direct_logs[name])
        if (
            direct_log is not None
            and paired_logs[name] is not None
            and not math.isclose(
                direct_log,
                paired_logs[name],
                rel_tol=0.0,
                abs_tol=LOG_ABS_TOL,
            )
        ):
            warnings.append(f"KOAWIN log{label} 与 {label} 不一致")

    fields = {
        "koawin_log_kow": paired_logs["kow"],
        "koawin_kow": coefficients["kow"],
        "koawin_log_koa": paired_logs["koa"],
        "koawin_koa": coefficients["koa"],
        "koawin_log_kaw": paired_logs["kaw"],
        "koawin_kaw": coefficients["kaw"],
    }
    return fields, warnings
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit the normalization unit**

```powershell
git add src/episuite_properties.py tests/test_episuite_properties.py
git commit -m "feat: normalize EPI partition coefficients"
```

---

### Task 2: Calculate TPSA and MR from the best available SMILES

**Files:**
- Modify: `src/episuite_properties.py`
- Modify: `tests/test_episuite_properties.py`

**Interfaces:**
- Consumes: `extract_koawin_partition_fields(data)`
- Produces: `calculate_rdkit_descriptor_fields(api_smiles=None, epi_smiles=None, input_smiles=None) -> tuple[dict[str, float | None], list[str]]`
- Produces: `build_epi_property_enrichment(data: dict, epi_smiles=None, input_smiles=None) -> tuple[dict[str, float | None], list[str]]`
- Task 3 consumes `build_epi_property_enrichment`.

- [ ] **Step 1: Append failing tests for SMILES priority, fallback, and non-fatal failure**

Add these methods to `EPISuitePropertyEnrichmentTests` in
`tests/test_episuite_properties.py`, and import
`build_epi_property_enrichment` and `calculate_rdkit_descriptor_fields`:

```python
    def test_rdkit_descriptors_prefer_api_smiles(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="CCO",
            epi_smiles="c1ccccc1",
            input_smiles="CC",
        )

        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertAlmostEqual(fields["mr_rdkit_cm3_mol"], 12.7598, places=6)
        self.assertEqual(warnings, [])

    def test_rdkit_descriptors_fall_back_to_input_smiles(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="",
            epi_smiles=None,
            input_smiles="CCO",
        )

        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertAlmostEqual(fields["mr_rdkit_cm3_mol"], 12.7598, places=6)
        self.assertEqual(warnings, [])

    def test_invalid_smiles_leaves_descriptors_empty_and_warns(self):
        fields, warnings = calculate_rdkit_descriptor_fields(
            api_smiles="not-a-smiles",
            epi_smiles=None,
            input_smiles=None,
        )

        self.assertIsNone(fields["tpsa_rdkit_a2"])
        self.assertIsNone(fields["mr_rdkit_cm3_mol"])
        self.assertEqual(
            warnings,
            ["RDKit 描述符未计算：SMILES 无法解析"],
        )

    def test_missing_smiles_leaves_descriptors_empty_and_warns(self):
        fields, warnings = calculate_rdkit_descriptor_fields()

        self.assertIsNone(fields["tpsa_rdkit_a2"])
        self.assertIsNone(fields["mr_rdkit_cm3_mol"])
        self.assertEqual(
            warnings,
            ["RDKit 描述符未计算：缺少可用 SMILES"],
        )

    def test_combined_enrichment_preserves_column_order(self):
        data = {
            "chemicalProperties": {"smiles": "CCO"},
            "logKow": {"estimatedValue": {"value": 3.0}},
            "logKoa": {
                "estimatedValue": {
                    "model": {
                        "kow": 1000.0,
                        "kaw": 0.01,
                        "koa": 100000.0,
                        "logKoa": 5.0,
                    }
                }
            },
        }

        fields, warnings = build_epi_property_enrichment(data)

        self.assertEqual(
            tuple(fields)[-2:],
            ("tpsa_rdkit_a2", "mr_rdkit_cm3_mol"),
        )
        self.assertAlmostEqual(fields["tpsa_rdkit_a2"], 20.23, places=6)
        self.assertEqual(warnings, [])
```

- [ ] **Step 2: Run the tests and verify the descriptor interfaces are missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties -v
```

Expected: FAIL because `calculate_rdkit_descriptor_fields` and
`build_epi_property_enrichment` cannot be imported.

- [ ] **Step 3: Add RDKit calculation and the combined enrichment interface**

Add these imports and functions to `src/episuite_properties.py`:

```python
from rdkit import Chem
from rdkit.Chem import Crippen, rdMolDescriptors


def _clean_smiles(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def calculate_rdkit_descriptor_fields(
    api_smiles=None,
    epi_smiles=None,
    input_smiles=None,
):
    fields = {
        "tpsa_rdkit_a2": None,
        "mr_rdkit_cm3_mol": None,
    }
    smiles = next(
        (
            cleaned
            for cleaned in (
                _clean_smiles(api_smiles),
                _clean_smiles(epi_smiles),
                _clean_smiles(input_smiles),
            )
            if cleaned
        ),
        "",
    )
    if not smiles:
        return fields, ["RDKit 描述符未计算：缺少可用 SMILES"]

    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return fields, ["RDKit 描述符未计算：SMILES 无法解析"]

    fields["tpsa_rdkit_a2"] = float(rdMolDescriptors.CalcTPSA(molecule))
    fields["mr_rdkit_cm3_mol"] = float(Crippen.MolMR(molecule))
    return fields, []


def build_epi_property_enrichment(data: dict, epi_smiles=None, input_smiles=None):
    partition_fields, partition_warnings = extract_koawin_partition_fields(data)
    chemical = data.get("chemicalProperties", {})
    api_smiles = chemical.get("smiles") if isinstance(chemical, dict) else None
    descriptor_fields, descriptor_warnings = calculate_rdkit_descriptor_fields(
        api_smiles=api_smiles,
        epi_smiles=epi_smiles,
        input_smiles=input_smiles,
    )
    return (
        {**partition_fields, **descriptor_fields},
        [*partition_warnings, *descriptor_warnings],
    )
```

- [ ] **Step 4: Run all property-enrichment tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties -v
```

Expected: 9 tests PASS.

- [ ] **Step 5: Commit the RDKit descriptor unit**

```powershell
git add src/episuite_properties.py tests/test_episuite_properties.py
git commit -m "feat: calculate EPI structure descriptors"
```

---

### Task 3: Integrate enrichment into Properties and Warnings

**Files:**
- Modify: `src/episuite_io.py:1-20,896-928,1005-1039`
- Modify: `tests/test_episuite_cas_values.py:600-657`

**Interfaces:**
- Consumes: `build_epi_property_enrichment(data, epi_smiles, input_smiles)`
- Preserves: `build_epi_web_result_tables(core_df=None, raw_df=None, warnings_df=None) -> dict[str, pd.DataFrame]`
- Produces: enrichment warning rows with `compound`, `smiles`, `cas`, and `warning`.

- [ ] **Step 1: Add failing categorized-table and warning integration tests**

Add `import copy` and `import math` to `tests/test_episuite_cas_values.py`.
Add these methods to `EPISuiteCasValueTests`:

```python
    def _response_with_koawin_model(self):
        response = copy.deepcopy(ETHANOL_CAS_AND_SMILES_RESPONSE)
        kow = 10.0 ** response["logKow"]["estimatedValue"]["value"]
        kaw = 0.001
        koa = kow / kaw
        response["logKoa"] = {
            "selectedValue": {
                "value": math.log10(koa),
                "units": "",
                "valueType": "ESTIMATED",
            },
            "estimatedValue": {
                "value": math.log10(koa),
                "units": "",
                "valueType": "ESTIMATED",
                "model": {
                    "kow": kow,
                    "kaw": kaw,
                    "koa": koa,
                    "logKoa": math.log10(koa),
                },
            },
            "experimentalValues": [],
        }
        return response

    def test_properties_include_partition_pairs_and_rdkit_descriptors(self):
        response = self._response_with_koawin_model()
        raw_rows = pd.DataFrame(
            [
                {
                    "compound": "Ethanol",
                    "smiles": "CC",
                    "cas": "64-17-5",
                    "epi_smiles": "c1ccccc1",
                    "raw_json": json.dumps(response),
                }
            ]
        )

        tables = episuite_io.build_epi_web_result_tables(raw_df=raw_rows)
        properties = tables["Properties"]

        expected_columns = [
            "koawin_log_kow",
            "koawin_kow",
            "koawin_log_koa",
            "koawin_koa",
            "koawin_log_kaw",
            "koawin_kaw",
            "tpsa_rdkit_a2",
            "mr_rdkit_cm3_mol",
        ]
        positions = [properties.columns.get_loc(name) for name in expected_columns]
        self.assertEqual(positions, list(range(positions[0], positions[0] + 8)))
        self.assertAlmostEqual(properties.loc[0, "tpsa_rdkit_a2"], 20.23)
        self.assertAlmostEqual(properties.loc[0, "mr_rdkit_cm3_mol"], 12.7598)
        self.assertTrue(
            math.isclose(
                properties.loc[0, "koawin_log_kaw"],
                math.log10(properties.loc[0, "koawin_kaw"]),
            )
        )
        self.assertNotIn("koawin_kow", tables["Core_Summary"].columns)
        self.assertNotIn("tpsa_rdkit_a2", tables["Core_Summary"].columns)
        self.assertNotIn("tpsa_rdkit_a2", tables["Raw_API_JSON"].columns)
        self.assertEqual(
            json.loads(tables["Raw_API_JSON"].loc[0, "raw_json"]),
            response,
        )
        self.assertTrue(tables["Warnings"].empty)

    def test_descriptor_failure_is_added_to_warnings_without_dropping_properties(self):
        response = self._response_with_koawin_model()
        response["chemicalProperties"]["smiles"] = "not-a-smiles"
        raw_rows = pd.DataFrame(
            [
                {
                    "compound": "Broken structure",
                    "smiles": "",
                    "cas": "",
                    "epi_smiles": "",
                    "raw_json": json.dumps(response),
                }
            ]
        )

        tables = episuite_io.build_epi_web_result_tables(raw_df=raw_rows)

        self.assertEqual(len(tables["Properties"]), 1)
        self.assertTrue(pd.isna(tables["Properties"].loc[0, "tpsa_rdkit_a2"]))
        self.assertEqual(
            tables["Warnings"].loc[0, "warning"],
            "RDKit 描述符未计算：SMILES 无法解析",
        )

```

- [ ] **Step 2: Run the integration tests and verify the new columns are absent**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_properties_include_partition_pairs_and_rdkit_descriptors tests.test_episuite_cas_values.EPISuiteCasValueTests.test_descriptor_failure_is_added_to_warnings_without_dropping_properties -v
```

Expected: FAIL because the `Properties` table does not contain the new columns.

- [ ] **Step 3: Import the enrichment helper and return property warnings**

In `src/episuite_io.py`, add:

```python
from src.episuite_properties import build_epi_property_enrichment
```

Replace the end of `_build_properties_row` with:

```python
    enrichment, warnings = build_epi_property_enrichment(
        data,
        epi_smiles=base.get("epi_smiles"),
        input_smiles=base.get("smiles"),
    )
    row.update(enrichment)
    return row, warnings
```

- [ ] **Step 4: Merge non-fatal property warnings into the existing Warnings table**

Update `build_epi_web_result_tables` so its property loop and return preparation are:

```python
    property_warning_rows = []

    for _, raw_row in raw.iterrows():
        data = _parse_raw_json(raw_row.get("raw_json"))
        base = _base_epi_identity(raw_row, data)
        properties_row, property_warnings = _build_properties_row(base, data)
        properties_rows.append(properties_row)
        for warning in property_warnings:
            property_warning_rows.append(
                {
                    "compound": base.get("compound"),
                    "smiles": base.get("smiles"),
                    "cas": base.get("cas"),
                    "warning": warning,
                }
            )
        degradation_rows.append(_build_degradation_row(base, data))
        fate_rows.append(_build_fate_transport_row(base, data))
        bioaccumulation_rows.append(_build_bioaccumulation_row(base, data))
        ecosar_rows.extend(_build_ecosar_rows(base, data))
        metadata_rows.extend(_build_metadata_rows(base, data))

    if property_warning_rows:
        warnings = pd.concat(
            [warnings, pd.DataFrame(property_warning_rows)],
            ignore_index=True,
            sort=False,
        )
```

Keep the existing return dictionary unchanged so `Warnings` still points to
the now-augmented `warnings` DataFrame.

- [ ] **Step 5: Run focused and existing EPI regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_cas_values -v
```

Expected: all tests in both modules PASS.

- [ ] **Step 6: Commit the result-table and workbook integration**

```powershell
git add src/episuite_io.py tests/test_episuite_cas_values.py
git commit -m "feat: expose EPI partition descriptors"
```

---

### Task 4: Add shared page/Excel labels and numeric display formats

**Files:**
- Create: `src/episuite_display.py`
- Create: `tests/test_episuite_display.py`
- Modify: `src/episuite_io.py:931-959`
- Modify: `tests/test_episuite_cas_values.py:640-657`
- Modify: `pages/3_EPISuite环境归趋.py:1-30,147-159`

**Interfaces:**
- Consumes the eight internal columns produced by Task 3.
- Produces: `episuite_property_column_config(frame, number_column_factory) -> dict`
- Produces: `episuite_property_export_frame(frame: pd.DataFrame) -> pd.DataFrame`
- Both helpers must leave the input DataFrame and its numeric values unchanged.

- [ ] **Step 1: Write failing display-policy and page-contract tests**

Create `tests/test_episuite_display.py`:

```python
import unittest
from pathlib import Path

import pandas as pd

from src.episuite_display import (
    episuite_property_column_config,
    episuite_property_export_frame,
)


class EPISuiteDisplayTests(unittest.TestCase):
    def test_builds_labels_and_formats_without_mutating_frame(self):
        frame = pd.DataFrame(
            {
                "compound": ["A"],
                "koawin_log_kow": [3.0],
                "koawin_kow": [1000.0],
                "koawin_log_koa": [5.0],
                "koawin_koa": [100000.0],
                "koawin_log_kaw": [-2.0],
                "koawin_kaw": [0.01],
                "tpsa_rdkit_a2": [20.23],
                "mr_rdkit_cm3_mol": [12.7598],
            }
        )
        original = frame.copy(deep=True)
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return kwargs

        config = episuite_property_column_config(frame, factory)

        self.assertEqual(config["koawin_log_kow"]["label"], "logKOW（KOAWIN估算）")
        self.assertEqual(config["koawin_kow"]["format"], "%.6e")
        self.assertEqual(config["tpsa_rdkit_a2"]["label"], "TPSA（Å²，RDKit）")
        self.assertEqual(config["mr_rdkit_cm3_mol"]["label"], "MR（cm³/mol，RDKit）")
        self.assertEqual(len(calls), 8)
        pd.testing.assert_frame_equal(frame, original)

        export_frame = episuite_property_export_frame(frame)
        self.assertIn("KOW（KOAWIN估算）", export_frame.columns)
        self.assertIn("TPSA（Å²，RDKit）", export_frame.columns)
        self.assertIsInstance(export_frame.loc[0, "KOW（KOAWIN估算）"], float)
        pd.testing.assert_frame_equal(frame, original)

    def test_page_uses_property_display_policy_only_for_properties(self):
        source = Path("pages/3_EPISuite环境归趋.py").read_text(encoding="utf-8")

        self.assertIn("episuite_property_column_config", source)
        self.assertIn('if sheet_name == "Properties"', source)
        self.assertIn("st.column_config.NumberColumn", source)
        self.assertIn("column_config=column_config", source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the display tests and verify the module is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.episuite_display'`.

- [ ] **Step 3: Implement the shared display policy**

Create `src/episuite_display.py`:

```python
from __future__ import annotations

import pandas as pd


PROPERTY_DISPLAY_SPECS = {
    "koawin_log_kow": ("logKOW（KOAWIN估算）", "%.6f"),
    "koawin_kow": ("KOW（KOAWIN估算）", "%.6e"),
    "koawin_log_koa": ("logKOA（KOAWIN估算）", "%.6f"),
    "koawin_koa": ("KOA（KOAWIN估算）", "%.6e"),
    "koawin_log_kaw": ("logKAW（KOAWIN估算）", "%.6f"),
    "koawin_kaw": ("KAW（KOAWIN估算）", "%.6e"),
    "tpsa_rdkit_a2": ("TPSA（Å²，RDKit）", "%.6f"),
    "mr_rdkit_cm3_mol": ("MR（cm³/mol，RDKit）", "%.6f"),
}


def episuite_property_column_config(
    frame: pd.DataFrame,
    number_column_factory,
) -> dict:
    return {
        column: number_column_factory(label=label, format=number_format)
        for column, (label, number_format) in PROPERTY_DISPLAY_SPECS.items()
        if column in frame.columns
    }


def episuite_property_export_frame(frame: pd.DataFrame) -> pd.DataFrame:
    labels = {
        column: label
        for column, (label, _) in PROPERTY_DISPLAY_SPECS.items()
        if column in frame.columns
    }
    return frame.rename(columns=labels)
```

- [ ] **Step 4: Apply the display policy only to the Properties tab**

Import the helper in `pages/3_EPISuite环境归趋.py`:

```python
from src.episuite_display import episuite_property_column_config
```

Replace the non-empty branch inside `render_epi_web_tables` with:

```python
            if table is not None and not table.empty:
                column_config = (
                    episuite_property_column_config(
                        table,
                        st.column_config.NumberColumn,
                    )
                    if sheet_name == "Properties"
                    else {}
                )
                st.dataframe(
                    table,
                    column_config=column_config,
                    width="stretch",
                )
```

- [ ] **Step 5: Apply the same labels to the Properties Excel worksheet**

Import the export helper in `src/episuite_io.py`:

```python
from src.episuite_display import episuite_property_export_frame
```

Change the worksheet loop in `build_result_workbook` to:

```python
        for sheet_name in EPI_WEB_RESULT_SHEETS:
            table = epi_tables.get(sheet_name, pd.DataFrame())
            export_table = (
                episuite_property_export_frame(table)
                if sheet_name == "Properties"
                else table
            )
            export_table.to_excel(writer, sheet_name=sheet_name, index=False)
```

Add this workbook test to `EPISuiteCasValueTests`:

```python
    def test_workbook_properties_sheet_uses_labels_and_keeps_values_numeric(self):
        response = self._response_with_koawin_model()
        input_df = pd.DataFrame(
            {"compound": ["Ethanol"], "smiles": ["CCO"], "cas": ["64-17-5"]}
        )
        raw_rows = pd.DataFrame(
            [
                {
                    "compound": "Ethanol",
                    "smiles": "CCO",
                    "cas": "64-17-5",
                    "raw_json": json.dumps(response),
                }
            ]
        )
        tables = episuite_io.build_epi_web_result_tables(raw_df=raw_rows)

        workbook_buffer = episuite_io.build_result_workbook(
            input_df,
            raw_df=raw_rows,
            epi_tables=tables,
        )
        workbook = load_workbook(workbook_buffer, data_only=True, read_only=True)
        worksheet = workbook["Properties"]
        rows = list(worksheet.iter_rows(values_only=True))
        header = list(rows[0])
        data = rows[1]

        for column in (
            "KOW（KOAWIN估算）",
            "KOA（KOAWIN估算）",
            "KAW（KOAWIN估算）",
            "TPSA（Å²，RDKit）",
            "MR（cm³/mol，RDKit）",
        ):
            self.assertIsInstance(data[header.index(column)], (int, float))
        self.assertNotIn("koawin_kow", header)
```

- [ ] **Step 6: Run display and EPI integration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_display tests.test_episuite_properties tests.test_episuite_cas_values -v
```

Expected: all tests in the three modules PASS.

- [ ] **Step 7: Commit the presentation and export layer**

```powershell
git add src/episuite_display.py src/episuite_io.py tests/test_episuite_display.py tests/test_episuite_cas_values.py pages/3_EPISuite环境归趋.py
git commit -m "feat: format EPI partition results"
```

---

### Task 5: Run repository-level verification

**Files:**
- Verify only; no planned source changes.

**Interfaces:**
- Consumes all deliverables from Tasks 1-4.
- Produces fresh evidence for focused behavior, complete regression, importability, and clean diffs.

- [ ] **Step 1: Run the focused EPI test modules**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_episuite_properties tests.test_episuite_display tests.test_episuite_cas_values -v
```

Expected: every test reports `ok` and the command exits with code 0.

- [ ] **Step 2: Run the full repository test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: `OK` with zero failures and zero errors.

- [ ] **Step 3: Compile application modules**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall app.py pages src
```

Expected: command exits with code 0 and reports no syntax errors.

- [ ] **Step 4: Check whitespace and committed scope**

Run:

```powershell
git diff --check
git status --short
git log -6 --oneline --decorate
```

Expected: `git diff --check` exits with code 0; status shows no uncommitted
changes in the files touched by this plan; recent commits include the four
feature commits from Tasks 1-4, this implementation-plan commit, and the
previously approved design commit `9c83011`.

- [ ] **Step 5: Review the final diff against the specification**

Run:

```powershell
git diff 9c83011..HEAD -- src/episuite_properties.py src/episuite_display.py src/episuite_io.py pages/3_EPISuite环境归趋.py tests/test_episuite_properties.py tests/test_episuite_display.py tests/test_episuite_cas_values.py
```

Expected: the diff contains the eight approved fields, RDKit calculations,
warning integration, page formatting, and tests; it contains no `ΔHvap`,
external API, dependency, cache, or checkpoint changes.
