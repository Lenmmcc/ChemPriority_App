# Combined EPA and ECHA Use Plot Design

## Goal

Add one exportable use plot that displays EPA CompTox and ECHA REACH results together for each compound while treating the two sources as parallel evidence streams.

## Visual Design

Each compound receives one polar chart divided by a vertical centre line:

- The left semicircle contains EPA CompTox top-use sectors.
- The right semicircle contains ECHA REACH top-use sectors.
- Each half independently allocates its 180 degrees according to that source's evidence counts. Evidence counts are never combined across sources.
- The same use category receives the same color in both halves. A shared use-category legend appears below the plot.
- `EPA` and `ECHA` labels appear above their respective halves, with a source key in the figure footer.
- If a source has no usable data for a compound, its half remains empty and displays `No EPA data` or `No ECHA data`.

## Data Flow

1. Reuse `extract_use_rose_data()` for each completed summary table, with source labels `EPA` and `ECHA`.
2. Concatenate the two long-form tables without merging evidence counts.
3. Generate a dedicated combined half-rose figure from that table.
4. Expose the combined figure in the existing `用途图谱` tab only when at least one source result exists.
5. Keep existing EPA-only and ECHA-only rose exports unchanged.

## Error Handling and Exports

- If only one result table exists, render its half and leave the other half empty.
- If evidence counts are absent or zero, use the existing equal-angle fallback independently within that source half.
- All export labels remain ASCII-safe for PDF/PNG portability.
- Provide combined PNG and PDF downloads named `EPA_ECHA_Combined_Use_Plot`.

## Verification

- Test equal 180-degree allocation within each source independently.
- Test source labels and empty-source annotations in the generated figure.
- Test a combined data set containing only EPA without failure.
- Run the complete unittest suite and Python compilation check.
