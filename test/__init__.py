"""测试包

包含项目的所有单元/集成测试。

模块：
    test_cleanup_logic    - 策略 B 清理逻辑测试（pipeline 临时文献清理）
    test_iteration_mock   - 颗粒度质量加权机制测试（V1→V2 迭代模拟）
    test_seed_pdf_dedupe  - 本地 PDF 入库去重逻辑测试

用法：
    # 跑全部测试
    python -m unittest discover -s test -v

    # 跑单个测试模块
    python -m unittest test.test_seed_pdf_dedupe -v

    # 直接跑（不走 unittest CLI）
    python test/test_seed_pdf_dedupe.py
    python test/test_cleanup_logic.py "causal inference"
    python test/test_iteration_mock.py
"""
