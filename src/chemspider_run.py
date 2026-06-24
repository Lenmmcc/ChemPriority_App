from dataclasses import dataclass
from typing import Optional


MISSING_CHEMSPIDER_KEY_MESSAGE = (
    "已跳过 ChemSpider：本次运行需要输入 ChemSpider API Key。"
)


@dataclass(frozen=True)
class ChemSpiderRunOptions:
    enabled: bool
    api_key: Optional[str]
    warning: str


def prepare_chemspider_run_options(use_chemspider, raw_api_key):
    if not use_chemspider:
        return ChemSpiderRunOptions(False, None, "")

    api_key = str(raw_api_key or "").strip()
    if not api_key:
        return ChemSpiderRunOptions(False, None, MISSING_CHEMSPIDER_KEY_MESSAGE)

    return ChemSpiderRunOptions(True, api_key, "")
