import pandas as pd


INPUT_IDENTITY_KEY = "input_identity_key"


def input_identity_key(row) -> str:
    value = row.get(INPUT_IDENTITY_KEY, row.get("identity_key", ""))
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def attach_input_identity(frame, row) -> pd.DataFrame:
    output = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
    key = input_identity_key(row)
    if INPUT_IDENTITY_KEY in output.columns:
        output[INPUT_IDENTITY_KEY] = output[INPUT_IDENTITY_KEY].fillna(key)
    else:
        output.insert(0, INPUT_IDENTITY_KEY, key)
    return output
