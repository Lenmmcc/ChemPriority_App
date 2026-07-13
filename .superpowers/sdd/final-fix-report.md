# Final review fix report

## Scope completed

1. Page 6 now passes its already prepared structure frame into the auto workflow without a second MOL/SMILES preparation pass. The exported `Structure_Preparation` table keeps the original `smiles_source` and decision warning.
2. The EPI result-workbook download and Page 4 resolver-tab direct download both append `Structure_Preparation`.
3. Page 0 defaults now use the shared MOL-column detector and preselect the detected column while preserving manual selection.
4. ECHA dossier rows now include `query_source`, `query_value`, and `is_primary_identity`.
5. When name and SMILES are both supplied alongside a stable ID, CompTox and ECHA run an input-identifier variant first, then retain independent name and SMILES variants. The input-identifier result is primary when it resolves.

## TDD evidence

Added or strengthened focused regressions for prepared MOL audit preservation, Page 0 MOL auto-detection, direct-download audit-sheet wiring, ECHA dossier provenance, and stable-ID three-variant provenance. The focused command was RED with all five intended failures before the production changes, then GREEN with all five passing.

## Verification

- Focused relevant suites: 64 tests passed.
- Full suite: `python -m unittest discover -s tests -v` — 169 tests passed.
- Compile: `python -m compileall app.py pages src` — completed successfully.
- Workbook smoke: actual EPI and resolver direct-download buffers both contained `Structure_Preparation`.
- Diff whitespace check: `git diff --check` passed.
