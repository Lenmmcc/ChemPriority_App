# EPI Suite Name-Only Query Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow every shared EPI Suite query entry point to resolve and query rows that contain only an exact chemical name.

**Architecture:** Relax the shared EPI input contract so `compound` is the sole required field, then add a cached client for the official EPI Suite `/api/search` endpoint. `run_epi_web_batch` will resolve missing SMILES by an exact, case-insensitive name match before using the existing CAS+SMILES submission and CAS-fallback flow. The auto-query workflow will treat either a compound name or SMILES as queryable.

**Tech Stack:** Python 3, pandas, urllib, Streamlit, unittest, unittest.mock, openpyxl

## Global Constraints

- Accept `name`, `chemical_name`, and existing aliases through the current normalization path.
- Match names only after trimming leading/trailing whitespace and applying case-insensitive comparison.
- When multiple exact matches exist, use the first item in the official EPI Suite response order.
- Never fall back to a fuzzy result or PubChem when no exact EPI Suite name match exists.
- Preserve the current CAS 404-to-SMILES fallback rule and all existing result fields.
- Keep failures isolated per row and record the failure reason.
- Preserve unrelated working-tree files and stage only files named in each task.

---

## File Structure

- Modify `src/episuite_io.py`: own the relaxed input contract, name-search client, exact-match resolver, batch integration, and name-safe ZIP generation.
- Modify `pages/3_EPISuite环境归趋.py`: describe name-only input and exact-name resolution in the user interface.
- Modify `src/auto_query_workflow.py`: allow name-only EPI rows through initial execution and retry gates.
- Modify `tests/test_episuite_cas_values.py`: cover validation, ZIP output, official search behavior, batch resolution, traceability, and failure isolation.
- Modify `tests/test_structure_preparation_page_contract.py`: lock the third-page name-only instructions into the page contract.
- Modify `tests/test_auto_query_workflow.py`: cover initial and retry behavior for name-only EPI rows.

### Task 1: Relax the input contract and make fallback exports name-safe

**Files:**
- Modify: `src/episuite_io.py:23-24,244-347`
- Modify: `pages/3_EPISuite环境归趋.py:201-215,241-243,299-304,382-399`
- Test: `tests/test_episuite_cas_values.py`
- Test: `tests/test_structure_preparation_page_contract.py`

**Interfaces:**
- Consumes: existing `normalize_input_columns(df)`, `_clean_optional_text(value)`, and `input_columns_for_display(df)`.
- Produces: `validate_input(df) -> tuple[bool, str]` that requires only `compound`; `build_input_zip(df) -> io.BytesIO` that accepts missing SMILES; unchanged public function names for all callers.

- [ ] **Step 1: Write failing input and ZIP tests**

Add `import zipfile` to `tests/test_episuite_cas_values.py`, then add:

```python
    def test_name_alias_without_smiles_is_valid_epi_input(self):
        normalized = episuite_io.normalize_input_columns(
            pd.DataFrame({"name": [" Ethanol "]})
        )

        valid, message = episuite_io.validate_input(normalized)

        self.assertTrue(valid, message)
        self.assertEqual(normalized.loc[0, "compound"], "Ethanol")
        self.assertNotIn("smiles", normalized.columns)

    def test_name_only_input_zip_contains_query_terms_without_blank_smiles(self):
        package = episuite_io.build_input_zip(
            pd.DataFrame({"compound": ["Ethanol", "Benzene"], "smiles": [pd.NA, "c1ccccc1"]})
        )

        with zipfile.ZipFile(package) as archive:
            self.assertEqual(
                archive.read("episuite_query_terms.txt").decode("utf-8"),
                "Ethanol\nc1ccccc1\n",
            )
            self.assertEqual(
                archive.read("episuite_smiles_only.txt").decode("utf-8"),
                "c1ccccc1\n",
            )
            self.assertIn(
                "Ethanol",
                archive.read("episuite_input.csv").decode("utf-8"),
            )
```

Add this contract test to `tests/test_structure_preparation_page_contract.py`:

```python
    def test_epi_page_advertises_name_only_input_and_exact_matching(self):
        source = _page_source("3")

        self.assertIn("name 或 compound", source)
        self.assertIn("名称完全一致", source)
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_alias_without_smiles_is_valid_epi_input tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_only_input_zip_contains_query_terms_without_blank_smiles tests.test_structure_preparation_page_contract.StructurePreparationPageContractTests.test_epi_page_advertises_name_only_input_and_exact_matching -v
```

Expected: three failures because validation still requires `smiles`, the ZIP assumes every row has SMILES, and the page lacks the new copy.

- [ ] **Step 3: Implement the relaxed contract and name-safe ZIP**

Change the constants and validation in `src/episuite_io.py`:

```python
REQUIRED_COLUMNS = ["compound"]
OPTIONAL_COLUMNS = ["smiles", "cas"]


def validate_input(df):
    missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_cols:
        return False, f"缺少必要列：{', '.join(missing_cols)}"

    empty_rows = df["compound"].isna().sum()
    if empty_rows > 0:
        return False, f"compound 存在空值，请先处理 {empty_rows} 行不完整数据。"

    duplicated = df["compound"].duplicated().sum()
    if duplicated > 0:
        return False, f"compound 存在 {duplicated} 个重复名称，请先确认是否需要合并或重命名。"

    return True, "输入数据检查通过。"
```

Update `make_template_file()` so its first example is name-only and the second retains a complete record:

```python
def make_template_file():
    template_df = pd.DataFrame(
        {
            "compound": ["Ethanol", "Benzene"],
            "smiles": ["", "c1ccccc1"],
            "cas": ["", "71-43-2"],
        }
    )
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        template_df.to_excel(writer, sheet_name="EPISuite_Input", index=False)
    buffer.seek(0)
    return buffer
```

Replace `build_input_zip()` with a version that does not index a missing SMILES column:

```python
def build_input_zip(df):
    clean_df = df[input_columns_for_display(df)].copy()
    if "smiles" not in clean_df.columns:
        clean_df["smiles"] = pd.NA
    if "cas" not in clean_df.columns:
        clean_df["cas"] = pd.NA

    clean_df["compound"] = clean_df["compound"].map(_clean_optional_text)
    clean_df["smiles"] = clean_df["smiles"].map(_clean_optional_text)
    clean_df["cas"] = clean_df["cas"].map(_clean_optional_text)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_buffer = io.StringIO()
    clean_df[["compound", "smiles", "cas"]].to_csv(csv_buffer, index=False)

    smiles_rows = clean_df.loc[clean_df["smiles"].ne("")]
    smiles_only = "".join(f"{value}\n" for value in smiles_rows["smiles"])
    named_smi = "".join(
        f"{row.smiles}\t{row.compound}\n"
        for row in smiles_rows.itertuples(index=False)
    )
    paste_list = "\n".join(smiles_rows["smiles"].tolist())
    query_terms = "".join(
        f"{row.smiles or row.compound}\n"
        for row in clean_df.itertuples(index=False)
    )

    readme = "\n".join(
        [
            "EPI Suite input package",
            "",
            "Files:",
            "- episuite_input.csv: compound, optional SMILES, and optional CAS.",
            "- episuite_query_terms.txt: SMILES when available, otherwise the chemical name.",
            "- episuite_smiles_only.txt: rows that already contain SMILES.",
            "- episuite_named.smi: available SMILES plus compound names.",
            "- episuite_paste_list.txt: available SMILES for direct copy/paste.",
            "",
            "Name-only rows must be resolved to an exact EPI Suite name match.",
        ]
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("episuite_input.csv", csv_buffer.getvalue())
        zf.writestr("episuite_query_terms.txt", query_terms)
        zf.writestr("episuite_smiles_only.txt", smiles_only)
        zf.writestr("episuite_named.smi", named_smi)
        zf.writestr("episuite_paste_list.txt", paste_list)
        zf.writestr("README.txt", readme)
        zf.writestr("manifest.txt", f"created_at={timestamp}\ncount={len(clean_df)}\n")
    zip_buffer.seek(0)
    return zip_buffer
```

Update the uploader help to:

```python
help="文件只需包含 name 或 compound；smiles、cas 可选。缺少 smiles 时仅接受 EPI Suite 名称完全一致的首个候选。"
```

```python
st.info("请先上传至少包含 name 或 compound 的 Excel 文件；smiles、cas 可选。")
```

```python
st.write(
    "点击后，系统会逐个调用 EPI Web Suite 网页端 API，并把结果整理成表格。"
    "缺少 smiles 时会先按名称搜索，并仅接受名称完全一致的首个候选；"
    "有 cas 时会同时提交 smiles 与 cas。"
)
```

Replace the fallback-package list with:

```python
    st.markdown(
        "\n".join(
            [
                "1. `episuite_query_terms.txt`：优先写入 SMILES；缺少 SMILES 时写入化合物名称。",
                "2. `episuite_smiles_only.txt`：仅包含已有 SMILES 的行。",
                "3. `episuite_named.smi`：已有 SMILES 与化合物名称，用于保留名称映射。",
                "4. `episuite_input.csv`：原始 compound + 可选 smiles + 可选 cas 表。",
                "5. `README.txt`：名称精确匹配规则与后续上传结果说明。",
            ]
        )
    )
```

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_alias_without_smiles_is_valid_epi_input tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_only_input_zip_contains_query_terms_without_blank_smiles tests.test_structure_preparation_page_contract.StructurePreparationPageContractTests.test_epi_page_advertises_name_only_input_and_exact_matching -v
```

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 5: Run the existing input/page regressions**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values tests.test_structure_preparation_page_contract -v
```

Expected: all tests pass. If the old missing-SMILES batch test fails only because batch integration is not implemented yet, keep that test unchanged for Task 3 and run all other named tests in this module before committing.

- [ ] **Step 6: Commit Task 1**

```powershell
git add -- src/episuite_io.py "pages/3_EPISuite环境归趋.py" tests/test_episuite_cas_values.py tests/test_structure_preparation_page_contract.py
git commit -m "feat: accept EPI name-only inputs"
```

### Task 2: Add the cached official EPI Suite name-search client

**Files:**
- Modify: `src/episuite_io.py:26,350-387`
- Test: `tests/test_episuite_cas_values.py`

**Interfaces:**
- Consumes: `DEFAULT_EPI_WEB_API`, `cached_call`, `_clean_optional_text`.
- Produces: `call_epi_web_search(query, api_url=DEFAULT_EPI_WEB_API, timeout=90, limit=100) -> list[dict]`; `resolve_epi_name_exact(compound, api_url=DEFAULT_EPI_WEB_API, timeout=90) -> dict`.

- [ ] **Step 1: Write failing search-client tests**

Add:

```python
    @patch("src.episuite_io.urllib.request.urlopen")
    def test_call_epi_web_search_uses_sibling_search_endpoint(self, urlopen):
        response = unittest.mock.MagicMock()
        response.read.return_value = json.dumps(
            [{"name": "ETHANOL", "smiles": "OCC", "cas": "000064-17-5"}]
        ).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response

        with cache_control(False):
            candidates = episuite_io.call_epi_web_search(
                " Ethanol ",
                api_url="https://example.test/api/submit",
            )

        request = urlopen.call_args.args[0]
        self.assertIn("/api/search?", request.full_url)
        self.assertIn("query=Ethanol", request.full_url)
        self.assertEqual(candidates[0]["cas"], "000064-17-5")

    @patch("src.episuite_io.call_epi_web_search")
    def test_exact_name_resolution_ignores_case_and_uses_first_exact_candidate(self, search):
        search.return_value = [
            {"name": "Ethanol derivative", "smiles": "CCC", "cas": "1-11-1"},
            {"name": " ETHANOL ", "smiles": "OCC", "cas": "000064-17-5"},
            {"name": "ethanol", "smiles": "CCO", "cas": "64-17-5"},
        ]

        resolved = episuite_io.resolve_epi_name_exact("Ethanol")

        self.assertEqual(resolved["name"], "ETHANOL")
        self.assertEqual(resolved["smiles"], "OCC")
        self.assertEqual(resolved["cas"], "000064-17-5")

    @patch("src.episuite_io.call_epi_web_search")
    def test_exact_name_resolution_rejects_fuzzy_only_candidates(self, search):
        search.return_value = [
            {"name": "Ethanol derivative", "smiles": "CCC", "cas": "1-11-1"}
        ]

        with self.assertRaisesRegex(RuntimeError, "完全一致"):
            episuite_io.resolve_epi_name_exact("Ethanol")

    @patch("src.episuite_io.call_epi_web_search")
    def test_exact_name_resolution_requires_candidate_smiles(self, search):
        search.return_value = [{"name": "ETHANOL", "smiles": "", "cas": "64-17-5"}]

        with self.assertRaisesRegex(RuntimeError, "SMILES"):
            episuite_io.resolve_epi_name_exact("Ethanol")
```

- [ ] **Step 2: Run search tests and verify RED**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_call_epi_web_search_uses_sibling_search_endpoint tests.test_episuite_cas_values.EPISuiteCasValueTests.test_exact_name_resolution_ignores_case_and_uses_first_exact_candidate tests.test_episuite_cas_values.EPISuiteCasValueTests.test_exact_name_resolution_rejects_fuzzy_only_candidates tests.test_episuite_cas_values.EPISuiteCasValueTests.test_exact_name_resolution_requires_candidate_smiles -v
```

Expected: errors stating that `call_epi_web_search` and `resolve_epi_name_exact` do not exist.

- [ ] **Step 3: Implement the cached search and exact resolver**

Add near `call_epi_web_api()`:

```python
def _epi_web_search_url(api_url):
    parsed = urllib.parse.urlsplit(api_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/submit"):
        path = f"{path[:-len('/submit')]}/search"
    else:
        path = f"{path}/search"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def call_epi_web_search(
    query,
    api_url=DEFAULT_EPI_WEB_API,
    timeout=90,
    limit=100,
):
    query = _clean_optional_text(query)
    if not query:
        return []
    search_url = _epi_web_search_url(api_url)
    return cached_call(
        "epi_web_search",
        "v1",
        {"search_url": search_url, "query": query, "limit": int(limit)},
        lambda: _call_epi_web_search_uncached(
            query,
            search_url=search_url,
            timeout=timeout,
            limit=limit,
        ),
    )


def _call_epi_web_search_uncached(query, search_url, timeout=90, limit=100):
    params = urllib.parse.urlencode({"query": query, "limit": int(limit)})
    separator = "&" if "?" in search_url else "?"
    request = urllib.request.Request(
        f"{search_url}{separator}{params}",
        headers={
            "Accept": "application/json",
            "User-Agent": "ChemPriority EPISuite connector",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"EPI Web Suite 名称搜索返回 HTTP {exc.code}: {body[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 EPI Web Suite 名称搜索: {exc.reason}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("EPI Web Suite 名称搜索返回了非列表结果。")
    return payload


def resolve_epi_name_exact(
    compound,
    api_url=DEFAULT_EPI_WEB_API,
    timeout=90,
):
    compound = _clean_optional_text(compound)
    candidates = call_epi_web_search(
        compound,
        api_url=api_url,
        timeout=timeout,
    )
    expected = compound.casefold()
    exact = next(
        (
            candidate
            for candidate in candidates
            if _clean_optional_text(candidate.get("name")).casefold() == expected
        ),
        None,
    )
    if exact is None:
        raise RuntimeError(f"名称“{compound}”没有名称完全一致的 EPI Suite 候选。")

    smiles = _clean_optional_text(exact.get("smiles"))
    if not smiles:
        raise RuntimeError(f"名称“{compound}”的精确候选缺少 SMILES。")
    return {
        "name": _clean_optional_text(exact.get("name")),
        "smiles": smiles,
        "cas": _clean_optional_text(exact.get("cas")),
    }
```

- [ ] **Step 4: Run search tests and verify GREEN**

Run the Step 2 command.

Expected: `Ran 4 tests ... OK`.

- [ ] **Step 5: Add and verify search-cache coverage**

Add:

```python
    @patch("src.episuite_io.urllib.request.urlopen")
    def test_call_epi_web_search_reuses_cached_response(self, urlopen):
        response = unittest.mock.MagicMock()
        response.read.return_value = b"[]"
        urlopen.return_value.__enter__.return_value = response

        with tempfile.TemporaryDirectory() as tmpdir:
            with use_cache_path(Path(tmpdir) / "queries.sqlite3"):
                first = episuite_io.call_epi_web_search("Ethanol")
                second = episuite_io.call_epi_web_search("Ethanol")

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        urlopen.assert_called_once()
```

Run:

```powershell
python -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_call_epi_web_search_reuses_cached_response -v
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add -- src/episuite_io.py tests/test_episuite_cas_values.py
git commit -m "feat: resolve exact EPI chemical names"
```

### Task 3: Integrate exact name resolution into the shared EPI batch

**Files:**
- Modify: `src/episuite_io.py:389-513`
- Test: `tests/test_episuite_cas_values.py`

**Interfaces:**
- Consumes: `resolve_epi_name_exact(compound, api_url, timeout) -> {"name": str, "smiles": str, "cas": str}` and existing `call_epi_web_api`.
- Produces: unchanged `run_epi_web_batch(...) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`, now with name-only support and actual submitted identifiers in results/raw rows.

- [ ] **Step 1: Replace the obsolete missing-SMILES test with failing name-resolution tests**

Replace `test_missing_smiles_is_rejected_without_api_call` and add the mixed-input test:

```python
    @patch("src.episuite_io.call_epi_web_api")
    @patch("src.episuite_io.resolve_epi_name_exact")
    def test_name_only_row_resolves_and_submits_exact_candidate(self, resolve_name, call_api):
        resolve_name.return_value = {
            "name": "ETHANOL",
            "smiles": "OCC",
            "cas": "000064-17-5",
        }
        call_api.return_value = ETHANOL_CAS_AND_SMILES_RESPONSE

        results, raw_rows, errors = episuite_io.run_epi_web_batch(
            pd.DataFrame({"compound": ["Ethanol"]}),
            delay_seconds=0,
        )

        resolve_name.assert_called_once_with(
            "Ethanol",
            api_url=episuite_io.DEFAULT_EPI_WEB_API,
            timeout=90,
        )
        call_api.assert_called_once_with(
            "OCC",
            cas="000064-17-5",
            api_url=episuite_io.DEFAULT_EPI_WEB_API,
            timeout=90,
        )
        self.assertEqual(results.loc[0, "smiles"], "OCC")
        self.assertEqual(results.loc[0, "cas"], "000064-17-5")
        self.assertIn("名称完全一致", results.loc[0, "query_note"])
        self.assertEqual(raw_rows.loc[0, "smiles"], "OCC")
        self.assertTrue(errors.empty)

    @patch("src.episuite_io.call_epi_web_api")
    @patch("src.episuite_io.resolve_epi_name_exact")
    def test_name_resolution_failure_isolated_without_submit(self, resolve_name, call_api):
        resolve_name.side_effect = RuntimeError("没有名称完全一致的 EPI Suite 候选")

        results, raw_rows, errors = episuite_io.run_epi_web_batch(
            pd.DataFrame({"compound": ["Unknown"]}),
            delay_seconds=0,
        )

        call_api.assert_not_called()
        self.assertTrue(raw_rows.empty)
        self.assertEqual(results.loc[0, "status"], "failed")
        self.assertIn("完全一致", errors.loc[0, "error"])

    @patch("src.episuite_io.call_epi_web_api")
    @patch("src.episuite_io.resolve_epi_name_exact")
    def test_mixed_name_and_smiles_rows_keep_order(self, resolve_name, call_api):
        resolve_name.return_value = {
            "name": "ETHANOL",
            "smiles": "OCC",
            "cas": "000064-17-5",
        }
        call_api.return_value = ETHANOL_CAS_AND_SMILES_RESPONSE
        input_df = pd.DataFrame(
            {
                "compound": ["Name only", "SMILES only", "SMILES and CAS"],
                "smiles": [pd.NA, "CC", "CCC"],
                "cas": [pd.NA, pd.NA, "3-33-3"],
            }
        )

        results, _, errors = episuite_io.run_epi_web_batch(
            input_df,
            delay_seconds=0,
            max_workers=3,
        )

        self.assertEqual(
            results["compound"].tolist(),
            ["Name only", "SMILES only", "SMILES and CAS"],
        )
        self.assertEqual(resolve_name.call_count, 1)
        self.assertTrue(errors.empty)
```

- [ ] **Step 2: Run batch tests and verify RED**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_only_row_resolves_and_submits_exact_candidate tests.test_episuite_cas_values.EPISuiteCasValueTests.test_name_resolution_failure_isolated_without_submit tests.test_episuite_cas_values.EPISuiteCasValueTests.test_mixed_name_and_smiles_rows_keep_order -v
```

Expected: failures because `run_epi_web_batch` still rejects missing SMILES.

- [ ] **Step 3: Implement name resolution inside `process_row`**

Add:

```python
def _join_query_notes(*notes):
    return "；".join(note for note in notes if _clean_optional_text(note))
```

In `run_epi_web_batch.process_row`, replace the missing-SMILES guard with:

```python
        if not smiles:
            try:
                resolved = resolve_epi_name_exact(
                    compound,
                    api_url=api_url,
                    timeout=timeout,
                )
            except Exception as exc:
                _append_failed_epi_row(
                    row_rows,
                    row_errors,
                    compound,
                    smiles,
                    cas,
                    f"EPI Suite 名称解析失败：{exc}",
                )
                return row_rows, row_raw_rows, row_errors
            smiles = resolved["smiles"]
            cas = resolved["cas"]
            query_note = (
                f"名称完全一致匹配：{resolved['name']}；"
                f"已使用解析得到的 SMILES"
            )
```

When the CAS fallback succeeds, preserve both notes:

```python
                    query_note = _join_query_notes(
                        query_note,
                        f"CAS 查询失败，已回退到 SMILES：{error_text}",
                    )
```

Do not change `_append_successful_epi_row`; it will now receive and store the resolved identifiers.

- [ ] **Step 4: Run batch tests and verify GREEN**

Run the Step 2 command.

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 5: Run the complete focused EPI regression**

Run:

```powershell
python -m unittest tests.test_episuite_cas_values tests.test_episuite_result_pool tests.test_episuite_supplement -v
```

Expected: all EPI tests pass, including the existing CAS 404 fallback tests.

- [ ] **Step 6: Commit Task 3**

```powershell
git add -- src/episuite_io.py tests/test_episuite_cas_values.py
git commit -m "feat: query EPI rows by exact name"
```

### Task 4: Allow name-only rows through auto-query and retry gates

**Files:**
- Modify: `src/auto_query_workflow.py:851-910,1125-1261`
- Test: `tests/test_auto_query_workflow.py`

**Interfaces:**
- Consumes: name-capable `run_epi_web_batch`.
- Produces: `queryable_epi_retry_input(retry_input) -> pd.DataFrame` filtered by non-empty `compound` OR non-empty `smiles`; initial execution uses the same filter.

- [ ] **Step 1: Write failing initial-run and retry tests**

In `test_identifier_runs_as_dependency_when_epi_is_selected`, make the resolver return empty identifiers:

```python
        run_identifier.return_value = (
            pd.DataFrame(
                {
                    "compound": ["Ethanol"],
                    "smiles": [""],
                    "cas": [""],
                    "ec": [""],
                    "dtxsid": [""],
                    "echa_id": [""],
                }
            ),
            pd.DataFrame(),
        )
```

Rename and update the retry tests:

```python
    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_queries_name_only_and_smiles_rows(self, run_epi):
        original = _result_with_epi_retry_input(
            ["Queryable B", "Name only C"]
        )
        for table_name in ("EPI_Results", "EPI_Retry_Input"):
            table = original.tables[table_name].copy()
            name_only = table["compound"].eq("Name only C")
            table.loc[name_only, ["smiles", "cas"]] = ""
            original.tables[table_name] = table
        run_epi.return_value = (
            complete_epi_rows(["Queryable B", "Name only C"]),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        retry_auto_workflow_epi_failures(
            original,
            AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
        )

        self.assertEqual(
            run_epi.call_args.args[0]["compound"].tolist(),
            ["Queryable B", "Name only C"],
        )

    @patch("src.auto_query_workflow.run_epi_web_batch")
    def test_retry_epi_skips_rows_without_name_or_smiles(self, run_epi):
        original = _result_with_epi_retry_input([""])
        retry_input = original.tables["EPI_Retry_Input"].copy()
        retry_input["compound"] = " "
        retry_input["smiles"] = " "
        original.tables["EPI_Retry_Input"] = retry_input

        retried = retry_auto_workflow_epi_failures(
            original,
            AutoWorkflowConfig(run_epi=True, epi_delay_seconds=0),
        )

        self.assertIs(retried, original)
        run_epi.assert_not_called()
```

- [ ] **Step 2: Run auto-query tests and verify RED**

Run:

```powershell
python -m unittest tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_identifier_runs_as_dependency_when_epi_is_selected tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_retry_epi_queries_name_only_and_smiles_rows tests.test_auto_query_workflow.AutoQueryWorkflowTests.test_retry_epi_skips_rows_without_name_or_smiles -v
```

Expected: the first two tests fail because blank-SMILES rows are skipped; the no-identity test continues to pass.

- [ ] **Step 3: Implement the shared name-or-SMILES filter**

Replace `queryable_epi_retry_input` with:

```python
def queryable_epi_retry_input(retry_input: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(retry_input, pd.DataFrame) or retry_input.empty:
        return pd.DataFrame(columns=getattr(retry_input, "columns", None))

    smiles = (
        retry_input["smiles"].map(_clean_text).ne("")
        if "smiles" in retry_input.columns
        else pd.Series(False, index=retry_input.index)
    )
    compound = (
        retry_input["compound"].map(_clean_text).ne("")
        if "compound" in retry_input.columns
        else pd.Series(False, index=retry_input.index)
    )
    return retry_input.loc[smiles | compound].copy().reset_index(drop=True)
```

Before the initial EPI condition, add:

```python
        network_query_input = queryable_epi_retry_input(resolution.query_input)
```

Replace the initial condition and batch argument:

```python
        if not network_query_input.empty:
            forward_epi_activity = activity_for(
                "EPI Suite 环境归趋",
                config.epi_timeout,
            )

            def record_epi_activity(event):
                attempt_events.append(dict(event))
                forward_epi_activity(event)

            epi_value = run_step(
                "EPI Suite 环境归趋",
                lambda: run_epi_web_batch(
                    network_query_input,
                    api_url=config.epi_api_url,
                    timeout=int(config.epi_timeout),
                    delay_seconds=float(config.epi_delay_seconds),
                    max_workers=int(config.epi_max_workers),
                    cache_enabled=bool(config.cache_enabled),
                    progress_callback=epi_progress,
                    activity_callback=record_epi_activity,
                ),
            )
            if epi_value is not None:
                network_results, network_raw, network_errors = epi_value
            else:
                network_results = pd.DataFrame()
                network_raw = pd.DataFrame()
                network_errors = pd.DataFrame()
            resolution = merge_network_epi(
                resolution,
                network_results,
                network_raw,
                network_errors,
                attempt_events,
            )
```

Replace the skip-status branch with:

```python
        elif network_query_input.empty:
            record(
                "EPI Suite 环境归趋",
                "跳过",
                0,
                "缺少可用于 EPI 的名称或 SMILES。",
            )
```

- [ ] **Step 4: Run auto-query tests and verify GREEN**

Run the Step 2 command.

Expected: `Ran 3 tests ... OK`.

- [ ] **Step 5: Run the full auto-query module**

Run:

```powershell
python -m unittest tests.test_auto_query_workflow -v
```

Expected: all tests pass. Update only assertions that explicitly encoded the former “SMILES required” rule; do not weaken unrelated identity, filename-association, checkpoint, or supplementation assertions.

- [ ] **Step 6: Commit Task 4**

```powershell
git add -- src/auto_query_workflow.py tests/test_auto_query_workflow.py
git commit -m "feat: allow EPI name-only workflow rows"
```

### Task 5: Final regression and requirements verification

**Files:**
- Verify: `src/episuite_io.py`
- Verify: `src/auto_query_workflow.py`
- Verify: `pages/3_EPISuite环境归趋.py`
- Verify: `tests/test_episuite_cas_values.py`
- Verify: `tests/test_auto_query_workflow.py`
- Verify: `tests/test_structure_preparation_page_contract.py`

**Interfaces:**
- Consumes: all preceding task deliverables.
- Produces: fresh evidence that the complete repository remains valid.

- [ ] **Step 1: Run targeted EPI and workflow regressions**

```powershell
python -m unittest tests.test_episuite_cas_values tests.test_episuite_result_pool tests.test_episuite_supplement tests.test_auto_query_workflow tests.test_structure_preparation_page_contract -v
```

Expected: all selected tests pass with zero failures and zero errors.

- [ ] **Step 2: Run the full repository test suite**

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 3: Compile the application**

```powershell
python -m compileall app.py pages src
```

Expected: exit code 0 with no syntax errors.

- [ ] **Step 4: Check patch hygiene**

```powershell
git diff --check
```

Expected: exit code 0 and no output.

- [ ] **Step 5: Review the requirement matrix**

Confirm from fresh test output and the final diff:

```text
[ ] name aliases normalize to compound
[ ] compound-only input validates
[ ] exact EPI search chooses the first exact candidate
[ ] fuzzy-only results fail without submission
[ ] resolved CAS and SMILES are traceable
[ ] mixed batches preserve order and isolate failures
[ ] fallback ZIP supports name-only rows
[ ] initial auto-query and retry accept names without SMILES
[ ] existing CAS fallback still passes
[ ] page copy describes the exact-match rule
```

Expected: every item is supported by a named passing test or a directly inspected page change.

- [ ] **Step 6: Inspect the final scope**

```powershell
git status --short --branch
git log --oneline -8
```

Expected: only the intended commits/files are part of this feature; pre-existing unrelated untracked files remain unmodified and unstaged.
