from __future__ import annotations

import math

from rdkit import Chem, rdBase
from rdkit.Chem import Crippen, rdMolDescriptors


PARTITION_COLUMN_ORDER = (
    "koawin_log_kow",
    "koawin_kow",
    "koawin_log_koa",
    "koawin_koa",
    "koawin_log_kaw",
    "koawin_kaw",
)
COEFFICIENT_REL_TOL = 1e-9
LOG_ABS_TOL = 1e-9


def _nested_value(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _finite_float(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive_float(value):
    number = _finite_float(value)
    return number if number is not None and number > 0 else None


def _coefficient_and_log(coefficient, direct_log):
    coefficient_value = _positive_float(coefficient)
    log_value = _finite_float(direct_log)
    if coefficient_value is None and log_value is not None:
        try:
            coefficient_value = 10.0 ** log_value
        except OverflowError:
            coefficient_value = None
    if coefficient_value is None or not math.isfinite(coefficient_value):
        return None, None
    return coefficient_value, math.log10(coefficient_value)


def extract_koawin_partition_fields(data: dict):
    model = _nested_value(data, "logKoa", "estimatedValue", "model")
    if not isinstance(model, dict):
        model = {}

    direct_logs = {
        "kow": _nested_value(data, "logKow", "estimatedValue", "value"),
        "koa": model.get("logKoa")
        if model.get("logKoa") is not None
        else _nested_value(data, "logKoa", "estimatedValue", "value"),
        "kaw": None,
    }
    coefficients = {}
    paired_logs = {}
    for name in ("kow", "koa", "kaw"):
        coefficients[name], paired_logs[name] = _coefficient_and_log(
            model.get(name),
            direct_logs[name],
        )

    warnings = []
    if all(coefficients[name] is not None for name in ("kow", "koa", "kaw")):
        expected_koa = coefficients["kow"] / coefficients["kaw"]
        if not math.isclose(
            coefficients["koa"],
            expected_koa,
            rel_tol=COEFFICIENT_REL_TOL,
            abs_tol=0.0,
        ):
            warnings.append("KOAWIN 原始系数关系不一致：KOA != KOW / KAW")

    for name, label in (("kow", "KOW"), ("koa", "KOA")):
        direct_log = _finite_float(direct_logs[name])
        if (
            direct_log is not None
            and paired_logs[name] is not None
            and not math.isclose(
                direct_log,
                paired_logs[name],
                rel_tol=0.0,
                abs_tol=LOG_ABS_TOL,
            )
        ):
            warnings.append(f"KOAWIN log{label} 与 {label} 不一致")

    fields = {
        "koawin_log_kow": paired_logs["kow"],
        "koawin_kow": coefficients["kow"],
        "koawin_log_koa": paired_logs["koa"],
        "koawin_koa": coefficients["koa"],
        "koawin_log_kaw": paired_logs["kaw"],
        "koawin_kaw": coefficients["kaw"],
    }
    return fields, warnings


def _clean_smiles(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "<na>"} else text


def calculate_rdkit_descriptor_fields(
    api_smiles=None,
    epi_smiles=None,
    input_smiles=None,
):
    fields = {
        "tpsa_rdkit_a2": None,
        "mr_rdkit_cm3_mol": None,
    }
    smiles = next(
        (
            cleaned
            for cleaned in (
                _clean_smiles(api_smiles),
                _clean_smiles(epi_smiles),
                _clean_smiles(input_smiles),
            )
            if cleaned
        ),
        "",
    )
    if not smiles:
        return fields, ["RDKit 描述符未计算：缺少可用 SMILES"]

    with rdBase.BlockLogs():
        molecule = Chem.MolFromSmiles(smiles)
    if molecule is None:
        return fields, ["RDKit 描述符未计算：SMILES 无法解析"]

    fields["tpsa_rdkit_a2"] = float(rdMolDescriptors.CalcTPSA(molecule))
    fields["mr_rdkit_cm3_mol"] = float(Crippen.MolMR(molecule))
    return fields, []


def build_epi_property_enrichment(data: dict, epi_smiles=None, input_smiles=None):
    partition_fields, partition_warnings = extract_koawin_partition_fields(data)
    chemical = data.get("chemicalProperties", {})
    api_smiles = chemical.get("smiles") if isinstance(chemical, dict) else None
    descriptor_fields, descriptor_warnings = calculate_rdkit_descriptor_fields(
        api_smiles=api_smiles,
        epi_smiles=epi_smiles,
        input_smiles=input_smiles,
    )
    return (
        {**partition_fields, **descriptor_fields},
        [*partition_warnings, *descriptor_warnings],
    )
