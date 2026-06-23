# ECHA and PubChem Identifier Completion Design

## Goal

Improve the fourth-page identifier completion flow so it resolves ECHA records in a safer order and can derive both a PubChem CID and SMILES from a supplied CAS Registry Number.

## Scope

- Change ECHA resolution order to `echa_id -> ec -> cas -> smiles -> compound`.
- Apply that order in direct ECHA use queries and in identifier completion.
- Extend the existing PubChem stage to resolve a CAS number to a PubChem CID and canonical SMILES.
- Preserve the existing SMILES-based PubChem lookup as a fallback.
- Update the export field guide and user-facing PubChem option text.
- Add isolated tests; no live third-party requests are permitted in the test suite.

## Data Flow

1. Normalize the six existing input fields: `compound`, `smiles`, `cas`, `ec`, `dtxsid`, and `echa_id`.
2. If PubChem is enabled and a CAS exists, request PubChem by CAS. Fill only missing `pubchem_cid`, `smiles`, `resolved_name`, `cas`, `ec`, and `dtxsid` values.
3. If the CAS request does not resolve a CID and SMILES is available, retry the existing SMILES-based PubChem lookup.
4. If no CAS is present but SMILES is available, retain the current SMILES-based lookup.
5. Pass all populated ECHA search fields to the ECHA resolver. It attempts them in the defined order and can fall back from an invalid or unmatched SMILES value to the compound name.

## Error Handling

- A failed CAS lookup does not discard the supplied CAS or block a SMILES fallback.
- No input-provided identifier is overwritten.
- Existing warning rows remain the channel for unresolved PubChem, EPA, and ECHA operations.
- ECHA's 2-40-character search limit remains an external constraint; a rejected SMILES lookup falls through to the next available identifier.

## Verification

- Test that the ECHA resolver attempts `smiles` before `compound`.
- Test that the identifier-completion ECHA request retains both SMILES and compound fallback values.
- Test a CAS-only PubChem response fills CID and SMILES.
- Test CAS failure with available SMILES falls back to the existing SMILES resolver.
- Run the full unittest suite and compile the modified modules.
