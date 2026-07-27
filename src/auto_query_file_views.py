from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import pandas as pd

from src.comptox_use import build_functional_use_table, build_product_use_table
from src.query_identity import INPUT_IDENTITY_KEY
from src.use_rose_plot import (
    build_compound_universe,
    extract_source_origin_pie_data,
    extract_top_product_use_category_data,
    extract_top_predicted_functional_use_data,
    extract_top_reported_functional_use_data,
)


@dataclass(frozen=True)
class FileModuleView:
    module_slug: str
    primary_file: str
    sample_id: str
    safe_export_name: str
    tables: OrderedDict[str, pd.DataFrame]
    charts: OrderedDict[str, object]


LOCAL_TABLES = (
    "Structure_Preparation",
    "Input_File_Mappings",
    "Input_Check",
    "Elemental_Ratios_DBE",
    "Category_Summary",
    "DF_Table",
    "Sample_Peak_Area",
    "Group_Area_Raw_Long",
    "Group_Area_Mean_By_Sample",
    "Plot_Warnings",
)
COMPT0X_RAW_TABLES = (
    "CompTox_Summary",
    "CompTox_Candidates",
    "CompTox_Errors",
)
ECHA_REACH_RAW_TABLES = (
    "ECHA_Use_Summary",
    "ECHA_Use_Candidates",
    "ECHA_Use_Dossiers",
    "ECHA_Use_Errors",
)
ECHA_GHS_RAW_TABLES = (
    "ECHA_GHS_Summary",
    "ECHA_GHS_Classifications",
    "ECHA_GHS_Errors",
)
SOURCE_RAW_TABLES = (
    "Source_Origin_Summary",
    "Source_Origin_Evidence",
    "Source_Origin_Errors",
)
ASSIGNMENT_AUDIT_TABLES = tuple(
    dict.fromkeys(
        (
            *COMPT0X_RAW_TABLES,
            *ECHA_REACH_RAW_TABLES,
            *ECHA_GHS_RAW_TABLES,
            *SOURCE_RAW_TABLES,
        )
    )
)


def scoped_chart_key(module_slug, safe_export_name, chart_name):
    return f"{module_slug}__{safe_export_name}__{chart_name}"


def safe_export_names(input_mappings) -> OrderedDict[str, str]:
    mappings = (
        input_mappings
        if isinstance(input_mappings, pd.DataFrame)
        else pd.DataFrame()
    )
    file_column = _first_column(mappings, ("file_name", "source_file", "primary_file"))
    if file_column is None:
        return OrderedDict()

    output = OrderedDict()
    used = set()
    for value in mappings[file_column]:
        file_name = _clean_text(value)
        if not file_name or file_name in output:
            continue
        stem = Path(file_name).stem
        base = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_") or "file"
        candidate = base
        if candidate.casefold() in used:
            digest = hashlib.sha256(file_name.casefold().encode("utf-8")).hexdigest()[:8]
            candidate = f"{base}_{digest}"
            sequence = 2
            while candidate.casefold() in used:
                candidate = f"{base}_{digest}_{sequence}"
                sequence += 1
        used.add(candidate.casefold())
        output[file_name] = candidate
    return output


def build_file_module_views(result):
    tables = result.tables
    mappings = tables.get("Input_File_Mappings", pd.DataFrame())
    membership = tables.get("EPI_Primary_Membership", pd.DataFrame())
    file_entries = _file_entries(mappings, membership)
    safe_names = safe_export_names(
        pd.DataFrame(
            {
                "file_name": [entry[0] for entry in file_entries],
                "sample_id": [entry[1] for entry in file_entries],
            }
        )
    )
    identity_files = _identity_file_map(membership)

    output = OrderedDict(
        (
            slug,
            OrderedDict(
                (
                    file_name,
                    FileModuleView(
                        module_slug=slug,
                        primary_file=file_name,
                        sample_id=sample_id,
                        safe_export_name=safe_names[file_name],
                        tables=OrderedDict(),
                        charts=OrderedDict(),
                    ),
                )
                for file_name, sample_id in file_entries
            ),
        )
        for slug in (
            "local_screening",
            "comptox_use",
            "echa_reach_use",
            "echa_ghs_cl",
            "echa",
            "source_origin",
        )
    )

    for file_name, sample_id in file_entries:
        local_tables = OrderedDict()
        for name in LOCAL_TABLES:
            frame = tables.get(name)
            if not isinstance(frame, pd.DataFrame):
                continue
            local_tables[name] = _local_file_rows(
                frame,
                name,
                file_name,
                sample_id,
                membership,
            )
        output["local_screening"][file_name] = _replace_view(
            output["local_screening"][file_name],
            tables=local_tables,
        )

    _populate_external_views(
        output["comptox_use"],
        tables,
        COMPT0X_RAW_TABLES,
        identity_files,
        membership,
        _rebuild_comptox_tables,
        "comptox_use",
    )
    _populate_external_views(
        output["echa_reach_use"],
        tables,
        ECHA_REACH_RAW_TABLES,
        identity_files,
        membership,
        _rebuild_echa_reach_tables,
        "echa_reach_use",
    )
    _populate_external_views(
        output["echa_ghs_cl"],
        tables,
        ECHA_GHS_RAW_TABLES,
        identity_files,
        membership,
        None,
        "echa_ghs_cl",
    )
    _populate_external_views(
        output["source_origin"],
        tables,
        SOURCE_RAW_TABLES,
        identity_files,
        membership,
        _rebuild_source_tables,
        "source_origin",
    )

    output["echa"] = _merge_echa_views(
        output["echa_reach_use"],
        output["echa_ghs_cl"],
    )
    _assign_scoped_charts(output, result.charts)
    return output


def file_assignment_warnings(result):
    membership = result.tables.get("EPI_Primary_Membership", pd.DataFrame())
    identity_files = _identity_file_map(membership)
    if not identity_files:
        return pd.DataFrame(
            columns=["stage", "table", "row_count", "message"]
        )

    rows = []
    for name in ASSIGNMENT_AUDIT_TABLES:
        frame = result.tables.get(name)
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            continue
        if INPUT_IDENTITY_KEY not in frame.columns:
            count = len(frame)
            reason = f"缺少 {INPUT_IDENTITY_KEY}"
        else:
            keys = frame[INPUT_IDENTITY_KEY].map(_clean_text)
            unknown = keys.eq("") | ~keys.isin(identity_files)
            count = int(unknown.sum())
            reason = "身份键为空或不在输入文件成员关系中"
        if not count:
            continue
        rows.append(
            {
                "stage": "File assignment",
                "table": name,
                "row_count": count,
                "message": (
                    f"{name} 有 {count} 行未归属到输入文件，"
                    f"已隔离到 unassigned（{reason}）。"
                ),
            }
        )
    return pd.DataFrame(
        rows,
        columns=["stage", "table", "row_count", "message"],
    )


def _populate_external_views(
    views,
    all_tables,
    raw_names,
    identity_files,
    membership,
    derived_builder,
    module_slug,
):
    unassigned = OrderedDict()
    for file_name, view in list(views.items()):
        selected = OrderedDict()
        identities = {
            identity
            for identity, files in identity_files.items()
            if file_name in files
        }
        for name in raw_names:
            frame = all_tables.get(name)
            if not isinstance(frame, pd.DataFrame):
                continue
            selected[name] = _identity_rows(frame, identities)
            unknown = _unassigned_identity_rows(frame, identity_files)
            if not unknown.empty:
                unassigned[name] = unknown
        if derived_builder is not None:
            selected.update(
                derived_builder(
                    selected,
                    _membership_for_file(membership, file_name),
                )
            )
        views[file_name] = _replace_view(view, tables=selected)

    if unassigned:
        safe_name = "unassigned"
        sample_id = "unassigned"
        selected = OrderedDict(unassigned)
        if derived_builder is not None:
            selected.update(
                derived_builder(
                    selected,
                    _membership_from_external_tables(selected),
                )
            )
        views["unassigned"] = FileModuleView(
            module_slug=module_slug,
            primary_file="unassigned",
            sample_id=sample_id,
            safe_export_name=safe_name,
            tables=selected,
            charts=OrderedDict(),
        )


def _rebuild_comptox_tables(tables, membership):
    candidates = tables.get("CompTox_Candidates", pd.DataFrame())
    universe = build_compound_universe(membership)
    return OrderedDict(
        [
            ("Product_Use_Categories", build_product_use_table(candidates)),
            (
                "Functional_Uses_Predicted",
                build_functional_use_table(
                    candidates,
                    functional_source="predicted",
                ),
            ),
            (
                "Functional_Uses_Reported",
                build_functional_use_table(
                    candidates,
                    functional_source="reported",
                ),
            ),
            (
                "EPA_PUC_Pie_Data",
                extract_top_product_use_category_data(candidates, universe),
            ),
            (
                "EPA_Predicted_Pie_Data",
                extract_top_predicted_functional_use_data(
                    candidates,
                    compound_universe=universe,
                ),
            ),
            (
                "EPA_Reported_Pie_Data",
                extract_top_reported_functional_use_data(
                    candidates,
                    universe,
                    source_label="EPA FC reported",
                    source_type="functional_use",
                    use_key="raw",
                    require_reported_flag=True,
                ),
            ),
        ]
    )


def _rebuild_echa_reach_tables(tables, membership):
    candidates = tables.get("ECHA_Use_Candidates", pd.DataFrame())
    universe = build_compound_universe(membership)
    return OrderedDict(
        [
            ("ECHA_Uses_Reported", candidates.copy()),
            (
                "ECHA_Reported_Pie_Data",
                extract_top_reported_functional_use_data(
                    candidates,
                    universe,
                    source_label="ECHA reported",
                    use_key="category",
                    require_reported_flag=False,
                ),
            ),
        ]
    )


def _rebuild_source_tables(tables, membership):
    summary = tables.get("Source_Origin_Summary", pd.DataFrame())
    universe = build_compound_universe(membership)
    return OrderedDict(
        [
            (
                "Source_Origin_Pie_Data",
                extract_source_origin_pie_data(summary, universe),
            )
        ]
    )


def _merge_echa_views(reach_views, ghs_views):
    output = OrderedDict()
    keys = list(reach_views)
    keys.extend(key for key in ghs_views if key not in reach_views)
    for key in keys:
        reach = reach_views.get(key)
        ghs = ghs_views.get(key)
        template = reach or ghs
        merged_tables = OrderedDict()
        merged_charts = OrderedDict()
        if reach is not None:
            merged_tables.update(reach.tables)
            merged_charts.update(reach.charts)
        if ghs is not None:
            merged_tables.update(ghs.tables)
            merged_charts.update(ghs.charts)
        output[key] = FileModuleView(
            module_slug="echa",
            primary_file=template.primary_file,
            sample_id=template.sample_id,
            safe_export_name=template.safe_export_name,
            tables=merged_tables,
            charts=merged_charts,
        )
    return output


def _assign_scoped_charts(output, charts):
    for module_slug, views in output.items():
        accepted_slugs = (
            ("echa", "echa_reach_use", "echa_ghs_cl")
            if module_slug == "echa"
            else (module_slug,)
        )
        for file_name, view in list(views.items()):
            selected = OrderedDict(view.charts)
            for chart_key, chart in charts.items():
                for accepted_slug in accepted_slugs:
                    prefix = (
                        f"{accepted_slug}__{view.safe_export_name}__"
                    )
                    if chart_key.startswith(prefix):
                        selected[chart_key[len(prefix):]] = chart
                        break
            views[file_name] = _replace_view(view, charts=selected)


def _replace_view(view, *, tables=None, charts=None):
    return FileModuleView(
        module_slug=view.module_slug,
        primary_file=view.primary_file,
        sample_id=view.sample_id,
        safe_export_name=view.safe_export_name,
        tables=view.tables if tables is None else tables,
        charts=view.charts if charts is None else charts,
    )


def _file_entries(mappings, membership):
    entries = []
    if isinstance(mappings, pd.DataFrame) and not mappings.empty:
        file_column = _first_column(
            mappings,
            ("file_name", "source_file", "primary_file"),
        )
        if file_column is not None:
            for _, row in mappings.iterrows():
                file_name = _clean_text(row.get(file_column))
                sample_id = _clean_text(row.get("sample_id")) or Path(file_name).stem
                if file_name and file_name not in {item[0] for item in entries}:
                    entries.append((file_name, sample_id))
    if not entries and isinstance(membership, pd.DataFrame):
        for _, row in membership.iterrows():
            file_name = _clean_text(row.get("primary_file"))
            sample_id = _clean_text(row.get("sample_id")) or Path(file_name).stem
            if file_name and file_name not in {item[0] for item in entries}:
                entries.append((file_name, sample_id))
    return entries


def _identity_file_map(membership):
    if (
        not isinstance(membership, pd.DataFrame)
        or membership.empty
        or "identity_key" not in membership.columns
        or "primary_file" not in membership.columns
    ):
        return {}
    output = OrderedDict()
    for identity, rows in membership.groupby("identity_key", sort=False):
        clean_identity = _clean_text(identity)
        if not clean_identity:
            continue
        output[clean_identity] = tuple(
            dict.fromkeys(
                _clean_text(value)
                for value in rows["primary_file"]
                if _clean_text(value)
            )
        )
    return output


def _identity_rows(frame, identities):
    if frame.empty:
        return frame.copy().reset_index(drop=True)
    if INPUT_IDENTITY_KEY not in frame.columns:
        return frame.iloc[0:0].copy().reset_index(drop=True)
    keys = frame[INPUT_IDENTITY_KEY].map(_clean_text)
    return frame.loc[keys.isin(identities)].copy().reset_index(drop=True)


def _unassigned_identity_rows(frame, identity_files):
    if frame.empty:
        return frame.copy().reset_index(drop=True)
    if INPUT_IDENTITY_KEY not in frame.columns:
        return frame.copy().reset_index(drop=True)
    keys = frame[INPUT_IDENTITY_KEY].map(_clean_text)
    return frame.loc[~keys.isin(identity_files)].copy().reset_index(drop=True)


def _local_file_rows(frame, name, file_name, sample_id, membership):
    if frame.empty:
        return frame.copy().reset_index(drop=True)
    if name == "Input_File_Mappings":
        file_column = _first_column(
            frame,
            ("file_name", "source_file", "primary_file"),
        )
        if file_column is not None:
            return frame.loc[
                frame[file_column].map(_clean_text).eq(file_name)
            ].copy().reset_index(drop=True)
    for column in ("sample_id", "source_sample_id"):
        if column in frame.columns:
            return frame.loc[
                frame[column].map(_clean_text).eq(sample_id)
            ].copy().reset_index(drop=True)
    member_rows = _membership_for_file(membership, file_name)
    compounds = {
        _clean_text(value).casefold()
        for value in member_rows.get("compound", pd.Series(dtype=object))
        if _clean_text(value)
    }
    compound_column = _first_column(frame, ("compound", "Name"))
    if compound_column is not None:
        return frame.loc[
            frame[compound_column]
            .map(lambda value: _clean_text(value).casefold())
            .isin(compounds)
        ].copy().reset_index(drop=True)
    return frame.iloc[0:0].copy().reset_index(drop=True)


def _membership_for_file(membership, file_name):
    if (
        not isinstance(membership, pd.DataFrame)
        or membership.empty
        or "primary_file" not in membership.columns
    ):
        return pd.DataFrame()
    return membership.loc[
        membership["primary_file"].map(_clean_text).eq(file_name)
    ].copy().reset_index(drop=True)


def _membership_from_external_tables(tables):
    for frame in tables.values():
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            columns = [
                column
                for column in ("compound", "cas", "smiles")
                if column in frame.columns
            ]
            if columns:
                return frame.loc[:, columns].drop_duplicates().reset_index(drop=True)
    return pd.DataFrame()


def _first_column(frame, candidates):
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return None


def _clean_text(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()
