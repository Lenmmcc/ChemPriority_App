import unittest

from src.auto_query_progress import (
    build_selected_steps,
    create_progress_state,
    format_activity_message,
    progress_snapshot,
    record_activity_event,
)


class AutoQueryProgressTests(unittest.TestCase):
    def test_selected_steps_include_dependencies_in_execution_order(self):
        steps = build_selected_steps(
            run_r_replicate_df=False,
            run_identifier=False,
            run_epi=True,
            run_comptox=False,
            run_echa_use=False,
            run_echa_ghs=False,
            run_source_origin=False,
            run_pov_lrtp_toxpi=True,
        )

        self.assertEqual(
            steps,
            [
                "化学类型图、DBE图、VK图与 DF",
                "标识符补全",
                "EPI Suite 环境归趋",
                "Pov-LRTP / PBM / ToxPi",
            ],
        )

    def test_activity_snapshot_shows_active_item_timeout_and_stage_progress(self):
        state = create_progress_state(["标识符补全", "EPI Suite 环境归趋"])
        record_activity_event(
            state,
            {
                "event": "started",
                "step": "EPI Suite 环境归趋",
                "index": 0,
                "total": 3,
                "done": 0,
                "label": "Diethyl phthalate",
                "timeout_seconds": 90,
            },
        )

        snapshot = progress_snapshot(state)
        self.assertEqual(snapshot["overall_finished"], 0)
        self.assertEqual(snapshot["overall_total"], 2)
        self.assertEqual(snapshot["module_done"], 0)
        self.assertEqual(snapshot["module_total"], 3)
        self.assertEqual(snapshot["active_labels"], ["Diethyl phthalate"])
        self.assertIn("90 秒", format_activity_message(snapshot))

        record_activity_event(
            state,
            {
                "event": "completed",
                "step": "EPI Suite 环境归趋",
                "index": 0,
                "total": 3,
                "done": 1,
                "label": "Diethyl phthalate",
                "elapsed_seconds": 7.2,
            },
        )
        record_activity_event(
            state,
            {"event": "stage_finished", "step": "EPI Suite 环境归趋", "status": "完成"},
        )

        snapshot = progress_snapshot(state)
        self.assertEqual(snapshot["overall_finished"], 1)
        self.assertEqual(snapshot["module_done"], 3)
        self.assertIn("最近完成", format_activity_message(snapshot))

    def test_new_stage_resets_the_current_module_counter(self):
        state = create_progress_state(["标识符补全", "EPI Suite 环境归趋"])
        record_activity_event(
            state,
            {"event": "completed", "step": "标识符补全", "index": 2, "total": 3, "done": 3},
        )
        record_activity_event(
            state,
            {"event": "stage_finished", "step": "标识符补全", "status": "完成"},
        )
        record_activity_event(
            state,
            {
                "event": "started",
                "step": "EPI Suite 环境归趋",
                "index": 0,
                "total": 2,
                "done": 0,
                "label": "Ethanol",
            },
        )

        snapshot = progress_snapshot(state)
        self.assertEqual(snapshot["module_done"], 0)
        self.assertEqual(snapshot["module_total"], 2)


if __name__ == "__main__":
    unittest.main()
