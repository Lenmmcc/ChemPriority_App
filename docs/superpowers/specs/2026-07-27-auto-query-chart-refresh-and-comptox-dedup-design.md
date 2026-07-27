# Auto-Query Chart Refresh and CompTox Deduplication Design

## Goal

Make every per-file EPA CompTox, ECHA REACH, and source-origin distribution
chart reflect the tables exported beside it, and prevent repeated CompTox query
variants from duplicating the same evidence.

## Scope

- Fix one-click/checkpoint chart generation and final ZIP exports.
- Fix repeated CompTox candidate evidence and public detail tables.
- Preserve the existing PUC, predicted-use, reported-use, `Others`, rare-category
  grouping, font, PNG, PDF, and workbook contracts.
- Do not change page-4 chart semantics or external query coverage.

## Chart lifecycle

Per-file derived tables exist as soon as file membership is known, even before
an external module has run. Chart discovery must therefore require the raw
source table that proves the module produced a result:

- EPA CompTox: `CompTox_Candidates`
- ECHA REACH: `ECHA_Use_Candidates`
- Source origin: `Source_Origin_Summary`

Intermediate checkpoints keep already-correct cumulative chart bytes and add
newly available charts. A final chart build, where `completed_step` is `None`,
rebuilds every currently available derived chart. This replaces stale bytes
loaded from checkpoints created by older versions while preserving local
screening charts that have no derived chart source.

## CompTox evidence deduplication

Name, SMILES, and supplied-identifier variants remain independently resolved so
identity conflicts stay auditable in summary/error outputs. When multiple
variants resolve to the same DTXSID and return the same evidence row, keep only
the first evidence row. Distinct DTXSIDs or genuinely different evidence remain.

The same evidence-level deduplication is applied before building derived
per-file tables and pie data so older cached/checkpoint candidate frames are
also repaired. Public product-use and functional-use tables additionally remove
identical projected rows as a final compatibility safeguard.

## Verification

- Before the EPA step, no EPA per-file distribution chart is generated.
- At the EPA checkpoint, the chart is generated from populated EPA tables.
- A final build replaces intentionally injected stale chart bytes.
- Same-DTXSID name/SMILES evidence is emitted once; different-DTXSID conflict
  evidence remains separate.
- Focused chart/file-view/CompTox tests pass, followed by the full unittest suite
  and compileall.
- Rebuild the six charts from the supplied two-workbook result package and
  confirm their legends/counts match the workbook pie-data sheets.
