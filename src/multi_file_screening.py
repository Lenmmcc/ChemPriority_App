from __future__ import annotations

import io
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import pandas as pd

from src.cp_screening_workflow import (
    build_detection_frequency,
    build_group_area_mean_by_sample,
    build_peak_area_long,
)
from src.mol_structure_parser import (
    find_mol_text_column,
    prepare_structure_dataframe,
    summarize_structure_preparation,
)
from src.r_screening_replica import ScreeningConfig, run_screening_pipeline
from src.r_screening_replica.plots import save_boxplot_log_transformed, save_compound_bubble_plot
from src.upload_state import upload_bytes, upload_name


STANDARD_COMPOUND_COL = "Name"
STANDARD_FORMULA_COL = "formula"
STANDARD_SMILES_COL = "SMILES_input"
STANDARD_CAS_COL = "CAS_input"


@dataclass
class PrimaryWorkbook:
    file_name: str
    sample_id: str
    data: pd.DataFrame
    content_bytes: bytes = b""


@dataclass(frozen=True)
class SampleColumnMapping:
    compound_col: str
    formula_col: str
    peak_area_col: str
    group_area_cols: tuple[str, ...] = ()
    mol_column: str | None = None
    smiles_col: str | None = None
    cas_col: str | None = None


@dataclass
class MultiFileScreeningResult:
    normalized_samples: list[dict]
    representative_table: pd.DataFrame
    structure_preparation: pd.DataFrame
    input_file_mappings: pd.DataFrame
    df_table: pd.DataFrame
    sample_peak_area: pd.DataFrame
    group_area_raw_long: pd.DataFrame
    group_area_mean_by_sample: pd.DataFrame
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    charts: dict = field(default_factory=dict)
    warnings: pd.DataFrame = field(default_factory=pd.DataFrame)
    output_root: str = ""
    screening_results: list[tuple[str, object]] = field(default_factory=list)
    summary_figure_paths: dict = field(default_factory=dict)
    structure_preparation_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    df_detection_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    selected_peak_cols: list[str] = field(default_factory=list)
    primary_membership: pd.DataFrame = field(default_factory=pd.DataFrame)
    epi_universe: pd.DataFrame = field(default_factory=pd.DataFrame)


def read_primary_workbooks(records) -> list[PrimaryWorkbook]:
    samples = []
    for record in records:
        data = upload_bytes(record)
        file_name = upload_name(record)
        frame = pd.read_excel(io.BytesIO(data))
        frame.columns = [str(column).strip() for column in frame.columns]
        samples.append(
            PrimaryWorkbook(
                file_name=file_name,
                sample_id=Path(file_name).stem,
                data=frame,
                content_bytes=data,
            )
        )
    _validate_primary_workbook_identities(samples)
    return samples


def is_group_area_column(column) -> bool:
    text = str(column).strip().lower().replace("_", " ")
    return text.startswith("group area")


def guess_peak_area_column(columns):
    for candidate in ["Group_Area", "Peak_Area", "Peak area", "Area"]:
        if candidate in columns:
            return candidate
    group_area_cols = [column for column in columns if is_group_area_column(column)]
    if group_area_cols:
        return group_area_cols[0]
    return columns[0] if columns else None


def default_sample_mapping(sample: PrimaryWorkbook) -> SampleColumnMapping:
    columns = list(sample.data.columns)
    compound_col = _guess_column(columns, ["Name", "compound", "Compound", "Chemical name"])
    formula_col = _guess_column(
        columns,
        ["formula", "Formula", "Molecular Formula", "NIST Lib Hit Formula"],
        fallback_index=0,
    )
    peak_area_col = guess_peak_area_column(columns)
    default_group_area_cols = _group_area_columns(columns) or (
        (peak_area_col,) if peak_area_col else ()
    )
    return SampleColumnMapping(
        compound_col=compound_col,
        formula_col=formula_col,
        peak_area_col=peak_area_col,
        group_area_cols=tuple(default_group_area_cols),
        mol_column=find_mol_text_column(columns),
    )


def build_upload_structure_preparation_preview(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summaries = []
    audits = []
    for sample in samples:
        mapping = mappings.get(sample.sample_id)
        if mapping is None:
            continue
        prepared = prepare_structure_dataframe(
            sample.data,
            mol_column=mapping.mol_column,
            smiles_column=mapping.smiles_col,
        )
        summaries.append(
            {"sample_id": sample.sample_id, **summarize_structure_preparation(prepared)}
        )
        audit = prepared.copy()
        audit.insert(0, "sample_id", sample.sample_id)
        audits.append(audit)
    return (
        pd.DataFrame(summaries),
        pd.concat(audits, ignore_index=True) if audits else pd.DataFrame(),
    )


def _build_primary_epi_membership_rows(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> pd.DataFrame:
    rows = []
    for sample in samples:
        mapping = mappings.get(sample.sample_id)
        if mapping is None:
            continue
        prepared = prepare_structure_dataframe(
            sample.data,
            mol_column=mapping.mol_column,
            smiles_column=mapping.smiles_col,
        )
        for position, (_, source_row) in enumerate(
            sample.data.iterrows(),
            start=2,
        ):
            compound = (
                _clean_text(source_row.get(mapping.compound_col))
                if mapping.compound_col
                else ""
            )
            smiles = _clean_text(prepared.iloc[position - 2].get("smiles"))
            cas = (
                _clean_text(source_row.get(mapping.cas_col))
                if mapping.cas_col
                else ""
            )
            if not any((compound, smiles, cas)):
                continue
            rows.append(
                {
                    "primary_file": sample.file_name,
                    "sample_id": sample.sample_id,
                    "source_row": position,
                    "compound": compound,
                    "smiles": smiles,
                    "cas": cas,
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "primary_file",
            "sample_id",
            "source_row",
            "compound",
            "smiles",
            "cas",
        ],
    )


def build_primary_epi_identity_tables(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    membership = _build_primary_epi_membership_rows(samples, mappings)
    if membership.empty:
        annotated = membership.assign(
            identity_key=pd.Series(dtype=str),
            identity_status=pd.Series(dtype=str),
            identity_candidates=pd.Series(dtype=str),
        )
        universe = pd.DataFrame(
            columns=[
                "identity_key",
                "identity_status",
                "identity_candidates",
                "compound",
                "smiles",
                "cas",
                "primary_files",
                "source_refs",
            ]
        )
        return annotated, universe

    working = membership.copy().reset_index(drop=True)
    working["_cas_norm"] = working["cas"].map(
        lambda value: _clean_text(value).replace(" ", "")
    )
    working["_smiles_norm"] = working["smiles"].map(_clean_text)
    working["_name_norm"] = working["compound"].map(
        lambda value: " ".join(_clean_text(value).casefold().split())
    )

    assignments = {}
    group_status = {}
    group_candidates = {}

    for cas in sorted(
        value for value in working["_cas_norm"].unique() if value
    ):
        identity_key = f"cas:{cas}"
        positions = working.index[working["_cas_norm"].eq(cas)].tolist()
        for position in positions:
            assignments[position] = identity_key
        group_status[identity_key] = "resolved"
        group_candidates[identity_key] = []

    for smiles in sorted(
        value
        for value in working.loc[
            working["_cas_norm"].ne(""),
            "_smiles_norm",
        ].unique()
        if value
    ):
        cas_candidates = sorted(
            {
                f"cas:{cas}"
                for cas in working.loc[
                    working["_smiles_norm"].eq(smiles)
                    & working["_cas_norm"].ne(""),
                    "_cas_norm",
                ]
            }
        )
        if len(cas_candidates) <= 1:
            continue
        for identity_key in cas_candidates:
            group_status[identity_key] = "conflicting_smiles"
            group_candidates[identity_key] = cas_candidates

    for smiles in sorted(
        value
        for value in working.loc[
            working["_cas_norm"].eq(""),
            "_smiles_norm",
        ].unique()
        if value
    ):
        positions = working.index[
            working["_cas_norm"].eq("")
            & working["_smiles_norm"].eq(smiles)
        ].tolist()
        cas_candidates = sorted(
            {
                f"cas:{cas}"
                for cas in working.loc[
                    working["_smiles_norm"].eq(smiles)
                    & working["_cas_norm"].ne(""),
                    "_cas_norm",
                ]
            }
        )
        if len(cas_candidates) == 1:
            identity_key = cas_candidates[0]
        elif len(cas_candidates) > 1:
            identity_key = f"smiles:{smiles}:ambiguous"
            group_status[identity_key] = "ambiguous_smiles"
            group_candidates[identity_key] = cas_candidates
        else:
            identity_key = f"smiles:{smiles}"
        for position in positions:
            assignments[position] = identity_key
        group_status.setdefault(identity_key, "resolved")
        group_candidates.setdefault(identity_key, [])

    strong_name_candidates = {}
    for position, identity_key in assignments.items():
        name = working.at[position, "_name_norm"]
        if name:
            strong_name_candidates.setdefault(name, set()).add(identity_key)

    for name in sorted(
        value
        for value in working.loc[
            working["_cas_norm"].eq("")
            & working["_smiles_norm"].eq(""),
            "_name_norm",
        ].unique()
        if value
    ):
        positions = working.index[
            working["_cas_norm"].eq("")
            & working["_smiles_norm"].eq("")
            & working["_name_norm"].eq(name)
        ].tolist()
        candidates = sorted(strong_name_candidates.get(name, ()))
        if len(candidates) == 1:
            identity_key = candidates[0]
        elif len(candidates) > 1:
            identity_key = f"name:{name}:ambiguous"
            group_status[identity_key] = "ambiguous_name"
            group_candidates[identity_key] = candidates
        else:
            identity_key = f"name:{name}"
        for position in positions:
            assignments[position] = identity_key
        group_status.setdefault(identity_key, "resolved")
        group_candidates.setdefault(identity_key, [])

    annotated = membership.copy().reset_index(drop=True)
    annotated["identity_key"] = [
        assignments[position] for position in annotated.index
    ]
    annotated["identity_status"] = annotated["identity_key"].map(
        group_status
    )
    annotated["identity_candidates"] = annotated["identity_key"].map(
        lambda key: _json_audit(group_candidates.get(key, ()))
    )

    universe_rows = []
    for identity_key in sorted(set(assignments.values())):
        positions = sorted(
            position
            for position, candidate in assignments.items()
            if candidate == identity_key
        )
        rows = working.loc[positions]
        source_refs = sorted(
            [
                {
                    "primary_file": _clean_text(row.get("primary_file")),
                    "sample_id": _clean_text(row.get("sample_id")),
                    "source_row": int(row.get("source_row")),
                }
                for _, row in rows.iterrows()
            ],
            key=lambda item: (
                item["primary_file"].casefold(),
                item["sample_id"].casefold(),
                item["source_row"],
            ),
        )
        universe_rows.append(
            {
                "identity_key": identity_key,
                "identity_status": group_status[identity_key],
                "identity_candidates": _json_audit(
                    group_candidates.get(identity_key, ())
                ),
                "compound": _coalesced_identity_value(
                    rows,
                    "compound",
                ),
                "smiles": _coalesced_identity_value(rows, "smiles"),
                "cas": _coalesced_identity_value(rows, "cas"),
                "primary_files": _json_audit(
                    sorted(
                        {
                            _clean_text(value)
                            for value in rows["primary_file"]
                            if _clean_text(value)
                        },
                        key=str.casefold,
                    )
                ),
                "source_refs": _json_audit(source_refs),
            }
        )
    universe = pd.DataFrame(universe_rows)
    return annotated, universe


def build_primary_epi_membership(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> pd.DataFrame:
    membership, _ = build_primary_epi_identity_tables(samples, mappings)
    return membership


def build_primary_epi_universe(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> pd.DataFrame:
    _, universe = build_primary_epi_identity_tables(samples, mappings)
    return universe


def _coalesced_identity_value(rows: pd.DataFrame, column: str) -> str:
    candidates = []
    for _, row in rows.iterrows():
        value = _clean_text(row.get(column))
        if not value:
            continue
        candidates.append(
            (
                0 if _clean_text(row.get("cas")) else 1,
                0 if _clean_text(row.get("smiles")) else 1,
                value.casefold(),
                value,
            )
        )
    return min(candidates)[-1] if candidates else ""


def _json_audit(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_samples_for_mappings(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> tuple[list[dict], list[str], pd.DataFrame]:
    normalized_samples = []
    selected_peak_cols = []
    seen_peak_cols = set()
    warnings = []

    for sample in samples:
        mapping = mappings.get(sample.sample_id)
        if mapping is None:
            warnings.append(
                {
                    "stage": "column_mapping",
                    "sample_id": sample.sample_id,
                    "message": (
                        "Sample column mapping is missing; this file was excluded "
                        "from screening calculations."
                    ),
                }
            )
            continue
        frame = sample.data
        prepared = prepare_structure_dataframe(
            frame,
            mol_column=mapping.mol_column,
            smiles_column=mapping.smiles_col,
        )
        normalized = frame.copy()

        if mapping.compound_col in frame.columns:
            normalized[STANDARD_COMPOUND_COL] = frame[mapping.compound_col].map(_clean_text)
        else:
            normalized[STANDARD_COMPOUND_COL] = ""
            warnings.append(
                {
                    "stage": "column_mapping",
                    "sample_id": sample.sample_id,
                    "message": f"Compound column is missing: {mapping.compound_col}",
                }
            )

        normalized[STANDARD_FORMULA_COL] = (
            frame[mapping.formula_col]
            if mapping.formula_col in frame.columns
            else pd.NA
        )

        available_peak_cols = [
            column for column in mapping.group_area_cols if column in frame.columns
        ]
        for column in available_peak_cols:
            normalized[column] = frame[column]
            if column not in seen_peak_cols:
                selected_peak_cols.append(column)
                seen_peak_cols.add(column)

        if (
            mapping.peak_area_col
            and mapping.peak_area_col in frame.columns
            and mapping.peak_area_col not in normalized.columns
        ):
            normalized[mapping.peak_area_col] = frame[mapping.peak_area_col]

        normalized[STANDARD_SMILES_COL] = prepared["smiles"]
        if mapping.cas_col and mapping.cas_col in frame.columns:
            normalized[STANDARD_CAS_COL] = frame[mapping.cas_col]

        if not available_peak_cols:
            warnings.append(
                {
                    "stage": "column_mapping",
                    "sample_id": sample.sample_id,
                    "message": "No Group Area columns were selected for this file.",
                }
            )

        normalized_samples.append(
            {
                "name": sample.sample_id,
                "file_name": sample.file_name,
                "bytes": sample.content_bytes,
                "data": normalized,
                "column_mapping": mapping,
                "structure_preparation": prepared,
            }
        )

    warning_table = pd.DataFrame(warnings, columns=["stage", "sample_id", "message"])
    return normalized_samples, selected_peak_cols, warning_table


def build_representative_screening_table(
    samples,
    compound_col,
    formula_col,
    peak_area_col,
    sample_cols=None,
    smiles_col=None,
    cas_col=None,
    primary_membership=None,
):
    frames = []
    sample_cols = sample_cols or []
    for sample in samples:
        frame = sample["data"].copy()
        frame["sample_id"] = sample["name"]
        frame["_primary_file"] = sample.get("file_name", "")
        frame["_source_row"] = range(len(frame))
        frame["Name"] = frame[compound_col].map(_clean_text)
        frame["formula"] = frame[formula_col] if formula_col in frame.columns else pd.NA
        peak_area_cols = sample_cols or [peak_area_col]
        frame["Group_Area"] = _row_peak_area(frame, peak_area_cols)
        if smiles_col and smiles_col in frame.columns:
            frame["SMILES_input"] = frame[smiles_col]
        if cas_col and cas_col in frame.columns:
            frame["CAS_input"] = frame[cas_col]
        frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["Name", "formula", "Group_Area", "compound_key"])
    combined = pd.concat(frames, ignore_index=True)
    combined["compound_key"] = combined["Name"].map(_compound_key)
    combined = combined[combined["compound_key"].ne("")].copy()
    combined = combined.sort_values("Group_Area", ascending=False, na_position="last")
    output_cols = ["Name", "formula", "Group_Area", "compound_key"]
    if "SMILES_input" in combined.columns:
        output_cols.append("SMILES_input")
    if "CAS_input" in combined.columns:
        output_cols.append("CAS_input")
    selected = combined.drop_duplicates("compound_key", keep="first").copy()
    if (
        isinstance(primary_membership, pd.DataFrame)
        and not primary_membership.empty
        and "identity_key" in primary_membership.columns
    ):
        identity_by_source = _membership_identity_by_source(
            primary_membership
        )
        selected["identity_key"] = [
            identity_by_source.get(
                (
                    _clean_text(row.get("_primary_file")).casefold(),
                    _clean_text(row.get("sample_id")).casefold(),
                    _clean_text(row.get("_source_row")).casefold(),
                ),
                "",
            )
            for _, row in selected.iterrows()
        ]
        output_cols.append("identity_key")
    return selected[output_cols].reset_index(drop=True)


def _membership_identity_by_source(
    primary_membership: pd.DataFrame,
) -> dict[tuple[str, str, str], str]:
    candidates = {}
    for _, row in primary_membership.iterrows():
        source_key = (
            _clean_text(row.get("primary_file")).casefold(),
            _clean_text(row.get("sample_id")).casefold(),
            _clean_text(row.get("source_row")).casefold(),
        )
        identity_key = _clean_text(row.get("identity_key"))
        if all(source_key) and identity_key:
            candidates.setdefault(source_key, set()).add(identity_key)
    return {
        source_key: next(iter(identity_keys))
        for source_key, identity_keys in candidates.items()
        if len(identity_keys) == 1
    }


def prepare_multi_file_screening(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
    detection_threshold: float,
    axis_ranges,
) -> MultiFileScreeningResult:
    _validate_primary_workbook_identities(samples)
    output_root = Path(tempfile.mkdtemp(prefix="cp_screening_"))
    screening_results = []
    warnings = []
    normalized_samples, selected_peak_cols, mapping_warnings = (
        normalize_samples_for_mappings(samples, mappings)
    )
    if not mapping_warnings.empty:
        warnings.extend(mapping_warnings.to_dict("records"))

    for sample in normalized_samples:
        file_sample_cols = [
            column
            for column in sample["column_mapping"].group_area_cols
            if column in sample["data"].columns
        ]
        if not file_sample_cols:
            warnings.append(
                {
                    "stage": "R_front_half",
                    "sample_id": sample["name"],
                    "message": "No selected peak-area columns are present in this file.",
                }
            )
            continue
        mean_frame = sample["data"].copy()
        mean_frame["Group_Area_Mean"] = _row_peak_area(mean_frame, file_sample_cols)
        config = ScreeningConfig(
            compound_col=STANDARD_COMPOUND_COL,
            formula_col=STANDARD_FORMULA_COL,
            group_area_col="Group_Area_Mean",
            sample_cols=["Group_Area_Mean"],
            output_dir=output_root / _safe_path_name(sample["name"]) / "workbook",
            axis_ranges=axis_ranges,
        )
        try:
            result = run_screening_pipeline(_dataframe_to_excel_bytes(mean_frame), config=config)
            _replace_dbe_bubble_with_thresholded_plot(
                result,
                detection_threshold,
                axis_ranges,
            )
        except Exception as exc:
            warnings.append(
                {
                    "stage": "R_front_half",
                    "sample_id": sample["name"],
                    "message": str(exc),
                }
            )
        else:
            screening_results.append((sample["name"], result))
            for warning in result.warnings:
                warnings.append(
                    {
                        "stage": "R_front_half",
                        "sample_id": sample["name"],
                        "message": warning,
                    }
                )

    calculation_samples = []
    group_area_raw_frames = []
    group_area_mean_frames = []
    for sample in normalized_samples:
        file_sample_cols = [
            column
            for column in sample["column_mapping"].group_area_cols
            if column in sample["data"].columns
        ]
        if not file_sample_cols:
            continue
        calculation_frame = sample["data"].copy()
        calculation_frame["Group_Area_Mean"] = _row_peak_area(
            calculation_frame,
            file_sample_cols,
        )
        calculation_samples.append(
            {
                **sample,
                "data": calculation_frame,
            }
        )
        group_area_raw_frames.append(
            build_peak_area_long(
                [(sample["name"], sample["data"])],
                compound_col=STANDARD_COMPOUND_COL,
                formula_col=STANDARD_FORMULA_COL,
                peak_area_cols=file_sample_cols,
            )
        )
        group_area_mean_frames.append(
            build_group_area_mean_by_sample(
                [(sample["name"], sample["data"])],
                compound_col=STANDARD_COMPOUND_COL,
                formula_col=STANDARD_FORMULA_COL,
                peak_area_cols=file_sample_cols,
            )
        )

    df_table, df_detection_table = build_detection_frequency(
        [
            (sample["name"], sample["data"])
            for sample in calculation_samples
        ],
        compound_col=STANDARD_COMPOUND_COL,
        peak_area_col="Group_Area_Mean",
        detection_threshold=detection_threshold,
    )
    group_area_raw_long = _concat_nonempty(
        group_area_raw_frames,
        build_peak_area_long(
            [],
            compound_col=STANDARD_COMPOUND_COL,
            formula_col=STANDARD_FORMULA_COL,
            peak_area_cols=[],
        ),
    )
    if not group_area_raw_long.empty:
        group_area_raw_long = group_area_raw_long.sort_values(
            ["source_sample_id", "sample_id", "compound"]
        ).reset_index(drop=True)
    group_area_mean = _concat_nonempty(
        group_area_mean_frames,
        build_group_area_mean_by_sample(
            [],
            compound_col=STANDARD_COMPOUND_COL,
            formula_col=STANDARD_FORMULA_COL,
            peak_area_cols=[],
        ),
    )
    if not group_area_mean.empty:
        group_area_mean = group_area_mean.sort_values(
            ["source_sample_id", "compound"]
        ).reset_index(drop=True)
    summary_figure_paths = _build_summary_figure_paths(
        screening_results,
        group_area_mean,
        output_root,
    )
    structure_summaries = pd.DataFrame(
        [
            {
                "sample_id": sample["name"],
                **summarize_structure_preparation(sample["structure_preparation"]),
            }
            for sample in normalized_samples
        ]
    )
    structure_audits = []
    for sample in normalized_samples:
        audit = sample["structure_preparation"].copy()
        for column in [
            STANDARD_COMPOUND_COL,
            STANDARD_FORMULA_COL,
            STANDARD_SMILES_COL,
            STANDARD_CAS_COL,
        ]:
            if column in sample["data"].columns:
                audit[column] = sample["data"][column]
        audit.insert(0, "sample_id", sample["name"])
        structure_audits.append(audit)
    structure_preparation = (
        pd.concat(structure_audits, ignore_index=True)
        if structure_audits
        else pd.DataFrame()
    )
    input_file_mappings = _input_file_mappings(samples, mappings)
    warning_table = pd.DataFrame(warnings, columns=["stage", "sample_id", "message"])
    tables = {
        "Input_Check": _dataframe_with_sample(screening_results, "input_check"),
        "Elemental_Ratios_DBE": _dataframe_with_sample(
            screening_results,
            "all_formulas",
        ),
        "Category_Summary": _dataframe_with_sample(
            screening_results,
            "category_summary",
        ),
    }
    primary_membership, epi_universe = build_primary_epi_identity_tables(
        samples,
        mappings,
    )

    return MultiFileScreeningResult(
        normalized_samples=normalized_samples,
        representative_table=build_representative_screening_table(
            calculation_samples,
            STANDARD_COMPOUND_COL,
            STANDARD_FORMULA_COL,
            "Group_Area_Mean",
            sample_cols=["Group_Area_Mean"],
            smiles_col=STANDARD_SMILES_COL,
            cas_col=STANDARD_CAS_COL,
            primary_membership=primary_membership,
        ),
        structure_preparation=structure_preparation,
        input_file_mappings=input_file_mappings,
        df_table=df_table,
        sample_peak_area=group_area_mean,
        group_area_raw_long=group_area_raw_long,
        group_area_mean_by_sample=group_area_mean,
        tables=tables,
        warnings=warning_table,
        output_root=str(output_root),
        screening_results=screening_results,
        summary_figure_paths=summary_figure_paths,
        structure_preparation_summary=structure_summaries,
        df_detection_table=df_detection_table,
        selected_peak_cols=selected_peak_cols,
        primary_membership=primary_membership,
        epi_universe=epi_universe,
    )


def _guess_column(columns, candidates, fallback_index=0):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return columns[fallback_index] if columns else None


def _validate_primary_workbook_identities(
    samples: list[PrimaryWorkbook],
) -> None:
    duplicate_file_names = _duplicate_casefolded(
        [sample.file_name for sample in samples]
    )
    if duplicate_file_names:
        raise ValueError(
            "Duplicate primary file names are not allowed (case-insensitive): "
            + ", ".join(duplicate_file_names)
            + ". Rename the files before uploading them again."
        )

    duplicate_sample_ids = _duplicate_casefolded(
        [sample.sample_id for sample in samples]
    )
    if duplicate_sample_ids:
        raise ValueError(
            "Duplicate primary sample IDs/file stems are not allowed "
            "(case-insensitive): "
            + ", ".join(duplicate_sample_ids)
            + ". Rename the files so every filename stem is unique."
        )


def _duplicate_casefolded(values) -> list[str]:
    groups = {}
    for value in values:
        text = str(value)
        groups.setdefault(text.casefold(), []).append(text)
    return sorted(
        {
            item
            for group in groups.values()
            if len(group) > 1
            for item in group
        },
        key=str.casefold,
    )


def _group_area_columns(columns):
    return [column for column in columns if is_group_area_column(column)]


def _row_peak_area(frame, peak_area_cols):
    available_cols = [column for column in peak_area_cols if column in frame.columns]
    if not available_cols:
        return pd.Series(pd.NA, index=frame.index, dtype="float64")
    return frame[available_cols].apply(pd.to_numeric, errors="coerce").mean(
        axis=1,
        skipna=True,
    )


def _dataframe_to_excel_bytes(frame):
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def _safe_path_name(value):
    text = _clean_text(value) or "sample"
    return (
        "".join(
            char if char.isalnum() or char in "._- " else "_"
            for char in text
        ).strip()
        or "sample"
    )


def _replace_dbe_bubble_with_thresholded_plot(
    result,
    detection_threshold,
    axis_ranges,
):
    dbe_table = result.dbe_table.copy()
    peak_area = pd.to_numeric(dbe_table["peak_area"], errors="coerce")
    thresholded_dbe = dbe_table.loc[peak_area > detection_threshold].copy()
    figures_dir = result.config.output_path / "figures"
    result.figure_paths["compound_bubble_plot"] = save_compound_bubble_plot(
        thresholded_dbe,
        result.compound_categories,
        figures_dir,
        axis_ranges,
    )
    result.metadata["dbe_plot_threshold"] = detection_threshold


def _build_summary_figure_paths(screening_results, group_area_mean, output_root):
    if group_area_mean.empty or not screening_results:
        return {}

    category_frames = []
    for _sample_id, result in screening_results:
        if (
            isinstance(result.compound_categories, pd.DataFrame)
            and not result.compound_categories.empty
        ):
            category_frames.append(result.compound_categories)
    if not category_frames:
        return {}

    compound_categories = (
        pd.concat(category_frames, ignore_index=True)
        .drop_duplicates("Formula")
        .reset_index(drop=True)
    )
    summary_figures_dir = output_root / "summary" / "figures"
    summary_figures_dir.mkdir(parents=True, exist_ok=True)
    return {
        "boxplot_log_transformed": save_boxplot_log_transformed(
            group_area_mean,
            compound_categories,
            summary_figures_dir,
        )
    }


def _input_file_mappings(
    samples: list[PrimaryWorkbook],
    mappings: Mapping[str, SampleColumnMapping],
) -> pd.DataFrame:
    rows = []
    for sample in samples:
        mapping = mappings.get(sample.sample_id)
        available_group_area_cols = (
            [
                column
                for column in mapping.group_area_cols
                if column in sample.data.columns
            ]
            if mapping is not None
            else []
        )
        rows.append(
            {
                "sample_id": sample.sample_id,
                "file_name": sample.file_name,
                "mapping_status": "provided" if mapping is not None else "missing",
                "participating": bool(available_group_area_cols),
                "compound_col": mapping.compound_col if mapping is not None else None,
                "formula_col": mapping.formula_col if mapping is not None else None,
                "peak_area_col": mapping.peak_area_col if mapping is not None else None,
                "group_area_cols": (
                    list(mapping.group_area_cols) if mapping is not None else []
                ),
                "mol_column": mapping.mol_column if mapping is not None else None,
                "smiles_col": mapping.smiles_col if mapping is not None else None,
                "cas_col": mapping.cas_col if mapping is not None else None,
            }
        )
    return pd.DataFrame(rows)


def _concat_nonempty(frames, empty):
    available = [frame for frame in frames if not frame.empty]
    return pd.concat(available, ignore_index=True) if available else empty


def _dataframe_with_sample(screening_results, attr_name):
    frames = []
    for sample_id, result in screening_results:
        table = getattr(result, attr_name)
        if isinstance(table, pd.DataFrame) and not table.empty:
            frame = table.copy()
            frame.insert(0, "sample_id", sample_id)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _clean_text(value):
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _compound_key(value):
    return " ".join(_clean_text(value).lower().split())
