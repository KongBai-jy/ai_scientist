"""seed_pdf.py 去重逻辑单元测试

验证 ingest_local_pdf 的去重行为：
    1. 首次塞入应成功（ingested > 0, skipped=False）
    2. 重复运行（同 source）应被去重跳过（skipped=True, ingested=0）
    3. 不同 source 不应被误判为重复
    4. dedupe=False 时强制重塞（即使 source 已存在也塞）
    5. 重复运行后向量库总数应保持不变（去重生效的最终证据）

用法:
    # 用 unittest 框架跑（详细输出）
    python -m unittest test.test_seed_pdf_dedupe -v

    # 直接跑
    python test/test_seed_pdf_dedupe.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

# 测试用 PDF（项目自带，位于项目根的 docs/ 目录）
TEST_PDF = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "docs",
    "125道科学问题.pdf",
)
SOURCE_A = "Test Source A"
SOURCE_B = "Test Source B"


def _clear_chroma():
    """清空 Chroma 集合（用于测试间隔离）"""
    from services.chroma_service import ChromaService
    store = ChromaService().load_or_create()
    try:
        existing = store._collection.get()
        ids = existing.get("ids", [])
        if ids:
            store._collection.delete(ids=ids)
    except Exception as e:
        # 第一次没有文档可能报错，忽略
        pass


class TestSeedPdfDedupe(unittest.TestCase):
    """验证 ingest_local_pdf 的去重逻辑"""

    @classmethod
    def setUpClass(cls):
        """整个测试类初始化：验证测试 PDF 存在"""
        if not os.path.exists(TEST_PDF):
            raise FileNotFoundError(f"测试 PDF 不存在: {TEST_PDF}")

    def setUp(self):
        """每个测试前清空 Chroma，确保测试间相互独立"""
        _clear_chroma()

    def test_01_first_ingest_succeeds(self):
        """场景 1：首次塞入应成功"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        result = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)

        self.assertFalse(result.get("skipped"), "首次塞入不应跳过")
        self.assertNotIn("error", result, f"不应有错误: {result.get('error')}")
        self.assertGreater(result["ingested"], 0, "应入库 chunks > 0")
        self.assertGreater(result["total_chars"], 0, "总字符应 > 0")
        self.assertEqual(result["source"], SOURCE_A)
        print(f"  ✅ 首次塞入成功: {result['ingested']} chunks, {result['total_chars']} 字符")

    def test_02_repeat_ingest_skipped(self):
        """场景 2：重复运行（同 source）应被去重跳过"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        # 第一次塞入
        first = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)
        self.assertGreater(first["ingested"], 0)

        # 第二次塞入（同 source）→ 应跳过
        second = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)

        self.assertTrue(second.get("skipped"), "重复运行应被跳过")
        self.assertEqual(second["ingested"], 0, "跳过时 ingested 应为 0")
        self.assertEqual(second["source"], SOURCE_A)
        print(f"  ✅ 重复运行被跳过: source={second['source']!r}")

    def test_03_different_source_not_skipped(self):
        """场景 3：不同 source 不应被误判为重复"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        # 塞入 source A
        first = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)
        self.assertGreater(first["ingested"], 0)

        # 塞入 source B（不同 source）→ 不应跳过
        second = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_B)

        self.assertFalse(second.get("skipped"), "不同 source 不应被跳过")
        self.assertNotIn("error", second)
        self.assertGreater(second["ingested"], 0, "不同 source 应正常塞入")
        print(f"  ✅ 不同 source 正常塞入: {second['ingested']} chunks (source={second['source']!r})")

    def test_04_force_reingest_without_dedupe(self):
        """场景 4：dedupe=False 时强制重塞（即使 source 已存在）"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        # 第一次塞入（dedupe=True）
        first = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A, dedupe=True)
        self.assertGreater(first["ingested"], 0)

        # 第二次塞入（dedupe=False）→ 不应跳过
        second = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A, dedupe=False)

        self.assertFalse(second.get("skipped"), "dedupe=False 不应跳过")
        self.assertNotIn("error", second)
        self.assertGreater(second["ingested"], 0, "dedupe=False 应正常塞入")
        print(f"  ✅ dedupe=False 强制重塞: {second['ingested']} chunks")

    def test_05_library_count_stable_after_repeat(self):
        """场景 5：重复运行后向量库总数应保持不变（去重生效的最终证据）"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        # 第一次塞入
        first = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)
        self.assertGreater(first["ingested"], 0)

        # 记录塞入后的总数
        store = svc.chroma.load_or_create()
        count_after_first = store._collection.count()
        self.assertEqual(count_after_first, first["ingested"],
                         "首次塞入后总数应等于入库 chunks 数")

        # 第二次塞入（同 source，应被跳过）
        second = svc.ingest_local_pdf(TEST_PDF, source=SOURCE_A)
        self.assertTrue(second.get("skipped"))

        # 验证总数不变
        count_after_second = store._collection.count()
        self.assertEqual(count_after_first, count_after_second,
                         f"重复运行后总数应保持不变: {count_after_first} → {count_after_second}")
        print(f"  ✅ 库总数稳定: {count_after_first} → {count_after_second}（不变）")

    def test_06_nonexistent_file_raises(self):
        """场景 6：文件不存在应抛 FileNotFoundError"""
        from services.paper_search_service import PaperSearchService
        svc = PaperSearchService()

        with self.assertRaises(FileNotFoundError):
            svc.ingest_local_pdf("/nonexistent/path/fake.pdf", source="fake")
        print(f"  ✅ 文件不存在正确抛 FileNotFoundError")


def main():
    """直接运行模式（不用 unittest CLI）"""
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSeedPdfDedupe)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
