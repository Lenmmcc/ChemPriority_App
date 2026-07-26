from src.storage_paths import (
    resolve_storage_paths,
    reset_storage_root,
    save_storage_root,
)


SOURCE_LABELS = {
    "environment": "环境变量",
    "saved setting": "已保存设置",
    "default": "默认位置",
}


def _source_label(value):
    return SOURCE_LABELS.get(str(value), str(value))


def render_storage_location_controls(st_module, prefix, on_change=None):
    paths = resolve_storage_paths()
    if paths.warning:
        st_module.warning(paths.warning)
    st_module.caption(
        f"查询缓存：{paths.query_cache_path}"
        f"（{_source_label(paths.query_path_source)}）"
    )
    st_module.caption(
        f"断点缓存：{paths.checkpoint_root}"
        f"（{_source_label(paths.checkpoint_path_source)}）"
    )

    environment_locked = (
        paths.query_path_source == "environment"
        and paths.checkpoint_path_source == "environment"
    )
    root_value = "" if paths.storage_root is None else str(paths.storage_root)
    selected = st_module.text_input(
        "缓存与断点存储根目录（绝对路径）",
        value=root_value,
        disabled=environment_locked,
        key=f"{prefix}_storage_root",
    )
    if environment_locked:
        st_module.info(
            "当前查询缓存和断点缓存均由环境变量控制，页面设置不会覆盖管理员配置。"
        )
        return paths
    if (
        paths.query_path_source == "environment"
        or paths.checkpoint_path_source == "environment"
    ):
        st_module.info(
            "已保存的根目录只影响未被专用环境变量覆盖的存储路径。"
        )

    if st_module.button(
        "保存并切换",
        key=f"{prefix}_save_storage_root",
    ):
        try:
            paths = save_storage_root(selected)
        except (OSError, ValueError) as exc:
            st_module.error(f"缓存位置未更改：{exc}")
        else:
            if on_change is not None:
                on_change()
            st_module.success("缓存与断点存储位置已保存并切换。")
            st_module.rerun()

    if st_module.button(
        "恢复默认位置",
        key=f"{prefix}_reset_storage_root",
    ):
        try:
            paths = reset_storage_root()
        except OSError as exc:
            st_module.error(f"缓存位置未更改：{exc}")
        else:
            if on_change is not None:
                on_change()
            st_module.success("已恢复默认位置；旧目录中的数据未删除。")
            st_module.rerun()
    return paths
