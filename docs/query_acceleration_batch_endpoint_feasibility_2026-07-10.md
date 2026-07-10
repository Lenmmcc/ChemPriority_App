# Query Acceleration Batch Endpoint Feasibility

Date: 2026-07-10

This check focuses on whether ChemPriority should prefer external batch endpoints after adding local cache and conservative concurrency. The current implementation keeps existing result schemas, workbook layouts, and page flows unchanged.

## Conclusion

Do not switch the app to batch-endpoint-first behavior in this round. Local persistent caching plus conservative concurrency is the lower-risk speedup for the current code paths.

The only clear near-term batch candidate is PubChem post-resolution enrichment by CID. That can probably combine property and synonym retrieval after each row already has a CID, but name/CAS/SMILES to CID resolution still remains row-oriented in the current workflow. Treat this as a later optimization, not a prerequisite for the current acceleration work.

## Source-by-source notes

| Source | Current app path | Batch feasibility | Decision |
| --- | --- | --- | --- |
| PubChem PUG REST | `compound/name/.../cids/JSON`, `compound/smiles/cids/JSON`, then `compound/cid/{cid}/property/.../JSON` and `compound/cid/{cid}/synonyms/JSON` in `src/identifier_resolver.py` | CID property and synonym enrichment can likely be grouped once CIDs are known. Initial name/CAS/SMILES resolution remains per input term. | Record as a follow-up optimization for enrichment only. Do not change this round. |
| EPI Web Suite | `https://episuite.dev/api/submit` with one SMILES and optional CAS in `src/episuite_io.py` | Current connector uses a single-query submit endpoint. No stable batch contract is present in the app code. | Keep per-row calls, accelerated by cache and limited concurrency. |
| CompTox Dashboard/API | Optional configured API plus Dashboard HTML fallback by DTXSID/page in `src/comptox_use.py` | Dashboard fallback is page-oriented. A configured API may offer different capabilities, but this app cannot assume a stable public batch contract without API documentation/key confirmation. | Keep per-row calls. Do not batch HTML fallback. |
| ECHA use | `searchText`, `rmlId`, dossier list, registration numbers, and dossier HTML paths in `src/echa_use.py` | Current paths are keyed by one search term, one `rmlId`, or one dossier asset. | Keep per-row calls. |
| ECHA GHS / C&L | Overview by ECHA ID, then classification and pictogram paths by `classificationId` in `src/echa_ghs.py` | Current paths are keyed by one ECHA ID or one classification ID. | Keep per-row calls. |
| ChEBI via OLS | `https://www.ebi.ac.uk/ols4/api/search` with one query term in `src/source_origin.py` | Search is query-oriented. A stable multi-query batch endpoint is not used by the app. | Keep per-row calls. |
| COCONUT | `https://coconut.naturalproducts.net/api/search` with one query term in `src/source_origin.py` | Current call is one search query per term. No stable multi-query batch contract is used by the app. | Keep per-row calls. |

## Follow-up option

If more speed is needed after observing real workloads, the safest next step is a PubChem enrichment combiner:

1. Resolve rows to CIDs with the existing row-level logic.
2. Group unique CIDs that missed cache.
3. Fetch PubChem properties and synonyms in grouped CID requests.
4. Split grouped responses back to the existing row schema.

This should be guarded by tests proving that duplicate rows hit cache, output row order is unchanged, warning rows remain compatible, and all existing Excel exports keep the same columns.
