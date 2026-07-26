# Streamlit Dataframe Width Parameter Migration

## Goal

Remove the deprecated Streamlit `use_container_width` argument from every
Python page in the project while preserving the current full-width dataframe
layout.

## Scope

- Replace every `st.dataframe(..., use_container_width=True)` call with the
  equivalent `st.dataframe(..., width="stretch")`.
- Preserve all dataframe inputs, column configuration, index visibility, and
  surrounding page behavior.
- Do not change charts, download controls, scientific calculations, cache
  behavior, or workbook exports.
- Do not introduce a compatibility wrapper because the installed Streamlit
  version supports `width="stretch"` directly.

## Implementation

The migration is a mechanical keyword-argument replacement across the affected
files under `pages/`. Calls spanning multiple lines keep their existing layout
except for the renamed argument and value.

## Validation

1. Add a source-contract regression test that fails while any
   `use_container_width=True` dataframe call remains.
2. Run the test before implementation to verify the deprecated calls are
   detected.
3. Apply the minimal replacements.
4. Run the regression test again and run the relevant Streamlit page tests.
5. Run the full unit-test suite and `git diff --check`.

## Success Criteria

- No Python source file contains `use_container_width=True`.
- Every migrated dataframe remains configured with `width="stretch"`.
- Streamlit page tests and the full test suite pass.
- No unrelated source or behavior changes are included.
