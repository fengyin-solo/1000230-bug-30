"""审片意见一致性回归测试（标准库 unittest，无需额外依赖）：

运行：cd backend && .venv/bin/python -m unittest app.tests.test_review -v
"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.services.review import ReviewService
from app.store import store


class ReviewConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例用一份全新的服务与种子数据。
        store.reset()
        self.service = ReviewService()

    def test_seed_normalized_to_rounds(self) -> None:
        entry = self.service.get_entry(3)
        self.assertEqual(entry["审片轮次"], "2")
        self.assertEqual(len(entry["opinions"]), 2)
        self.assertEqual(entry["status"], "已通过")
        self.assertFalse(entry["pending"])

    def test_empty_opinion_rejected(self) -> None:
        entry, message, _ = self.service.run_action(2, "提交意见", {"问题类型": "x", "修改意见": "  "})
        self.assertIsNone(entry)
        self.assertIn("修改意见不能为空", message)

    def test_cannot_confirm_without_opinion(self) -> None:
        # 待审片记录没有任何意见，确认通过必须被拦。
        entry, message, _ = self.service.run_action(1, "确认通过", {})
        self.assertIsNone(entry)
        self.assertIn("提交意见", message)

    def test_submit_appends_round_and_syncs_snapshot(self) -> None:
        entry, _, _ = self.service.run_action(
            1, "提交意见", {"问题类型": "穿帮", "修改意见": "换镜头"}, idempotency_key="k1", expected_version=1
        )
        self.assertEqual(entry["审片轮次"], "1")
        self.assertEqual(entry["问题类型"], "穿帮")
        self.assertEqual(entry["status"], "待修改")
        # 列表与详情是同一份内存对象，快照与最新一轮意见一致。
        listed = next(row for row in self.service.list_entries()[0] if row["id"] == 1)
        detail = self.service.get_entry(1)
        self.assertIs(listed, detail)
        self.assertEqual(listed["修改意见"], detail["opinions"][-1]["修改意见"])

    def test_idempotent_retry_does_not_duplicate_round(self) -> None:
        self.service.run_action(
            1, "提交意见", {"问题类型": "穿帮", "修改意见": "原始意见"},
            idempotency_key="k1", expected_version=1,
        )
        # 模拟请求超时后同键重试：内容不同也必须被去重。
        entry, _, code = self.service.run_action(
            1, "提交意见", {"问题类型": "别的", "修改意见": "重试意见"},
            idempotency_key="k1", expected_version=2,
        )
        self.assertEqual(code, "idempotent")
        self.assertEqual(len(self.service.get_entry(1)["opinions"]), 1)
        self.assertEqual(self.service.get_entry(1)["修改意见"], "原始意见")

    def test_optimistic_concurrency(self) -> None:
        self.service.run_action(
            1, "提交意见", {"问题类型": "穿帮", "修改意见": "先到的一条"},
            idempotency_key="a", expected_version=1,
        )
        # 后提交者仍拿着旧版本号，必须冲突而不是覆盖前一条。
        entry, _, code = self.service.run_action(
            1, "提交意见", {"问题类型": "字幕", "修改意见": "后到的一条"},
            idempotency_key="b", expected_version=1,
        )
        self.assertIsNone(entry)
        self.assertEqual(code, "conflict")
        self.assertEqual(self.service.get_entry(1)["修改意见"], "先到的一条")
        # 刷新到最新版本后再提交，能看到前一条并追加为第二轮。
        latest = self.service.get_entry(1)
        entry, _, _ = self.service.run_action(
            1, "提交意见", {"问题类型": "字幕", "修改意见": "后到的一条"},
            idempotency_key="b", expected_version=latest["version"],
        )
        self.assertEqual(entry["审片轮次"], "2")
        self.assertEqual(len(entry["opinions"]), 2)

    def test_passed_record_is_terminal(self) -> None:
        entry, _, _ = self.service.run_action(3, "提交意见", {"问题类型": "x", "修改意见": "y"},
                                             idempotency_key="z")
        self.assertIsNone(entry)
        entry, _, _ = self.service.run_action(3, "确认通过", {})
        self.assertIsNone(entry)
        self.assertEqual(self.service.get_entry(3)["status"], "已通过")

    def test_overview_matches_current_status(self) -> None:
        overview = store.overview()
        review = next(item for item in overview["modules"] if item["name"] == "review")
        pending = sum(1 for row in store.rows("review") if row["status"] != "已通过")
        self.assertEqual(review["pending"], pending)


class ReviewHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        ReviewService()
        self.client = TestClient(app)

    def test_conflict_returns_409(self) -> None:
        response = self.client.post(
            "/api/review/2/actions",
            json={"values": {"action": "提交意见", "问题类型": "x", "修改意见": "y",
                             "expected_version": 999, "idempotency_key": "c"}},
        )
        self.assertEqual(response.status_code, 409)

    def test_empty_opinion_http(self) -> None:
        response = self.client.post(
            "/api/review/2/actions",
            json={"values": {"action": "提交意见", "问题类型": "x", "修改意见": ""}},
        )
        self.assertFalse(response.json()["ok"])
        self.assertIn("不能为空", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
