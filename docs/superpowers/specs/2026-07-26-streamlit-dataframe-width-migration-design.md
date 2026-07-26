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
- Require Streamlit 1.49 or newer because that release added string width
  values such as `width="stretch"`.
- Do not introduce a compatibility wrapper because the supported Streamlit
  versions accept `width="stretch"` directly.

## Implementation

The migration is a mechanical keyword-argument replacement across the affected
files under `pages/`. Calls spanning multiple lines keep their existing layout
except for the renamed argument and value.

## Validation

1. Add source-contract regression tests that fail while any
   `use_container_width=True` dataframe call remains or while the declared
   Streamlit minimum version is below 1.49.
2. Run the test before implementation to verify the deprecated calls are
   detected.
3. Apply the minimal replacements.
4. Run the regression test again and run the relevant Streamlit page tests.
5. Run the full unit-test suite and `git diff --check`.

## Success Criteria

- No production page contains `use_container_width=True`.
- Every migrated dataframe remains configured with `width="stretch"`.
- The declared Streamlit dependency is `streamlit>=1.49,<2`.
- Streamlit page tests and the full test suite pass.
- No unrelated source or behavior changes are included.
