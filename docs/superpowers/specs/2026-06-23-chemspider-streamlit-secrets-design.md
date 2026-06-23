# ChemSpider Optional Identifier Provider Design

## Goal

Add ChemSpider as an optional identifier-completion provider for the Streamlit deployment without committing or exposing its API key.

## Configuration

- The deployed app reads `CHEMSPIDER_API_KEY` from `st.secrets`.
- Local development reads `.streamlit/secrets.toml`; this exact file is ignored by Git.
- The resolver receives the key as an explicit function argument and never imports Streamlit.
- When the key is absent, the UI shows ChemSpider as unavailable and the batch skips it without warning or failure.

## Resolution Flow

1. Keep PubChem as the first general-purpose resolver.
2. Run ChemSpider only when a key is configured and one or more of canonical name, CAS, or structure identifiers remains absent.
3. Search ChemSpider by the strongest available identifier: InChIKey/SMILES, then CAS, then name.
4. Retrieve the chosen record's CSID, preferred name, SMILES, InChIKey, formula, and available CAS/synonyms.
5. Fill only blank fields; retain ChemSpider provenance in status and notes.
6. Continue with existing EPA and ECHA steps to resolve DTXSID, EC, and ECHA ID.

## Safety and Verification

- No key is added to source code, test fixtures, logs, exports, `.env` files, or Git commits.
- Network tests use mocked ChemSpider HTTP responses; a live test is manual and reads only the Streamlit Secret.
- Add unit tests for missing-key skip, response normalization, and non-overwriting merge behavior.
- Document the Streamlit Secrets entry without including a real value.
