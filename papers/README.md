# PDF 文献文件夹

存放用于构建本地知识库的 PDF 论文，覆盖 Science 125 前沿科学问题的 12 大领域。

## 目录结构

```
papers/
├── README.md                           ← 本文件
├── mathematical_sciences/              ← 数学科学
├── chemistry/                          ← 化学
├── medicine_health/                    ← 医学与健康
├── biology/                            ← 生物学
├── astronomy/                          ← 天文学
├── physics/                            ← 物理学
├── engineering_materials/              ← 工程与材料科学
├── information_science/                ← 信息科学
├── neuroscience/                       ← 神经科学
├── ecology/                            ← 生态学
├── energy_science/                     ← 能源科学
└── artificial_intelligence/            ← 人工智能
```

## 入库流程

```bash
# 单文件入库
python scripts/seed_pdf.py "papers/physics/ybco_2024.pdf" --source "physics_ybco_2024"

# 批量入库（扫描 papers/ 下所有 PDF，含子文件夹）
python scripts/seed_pdf_batch.py

# Dry-run 预览（不实际执行）
python scripts/seed_pdf_batch.py --dry-run

# 验证
python scripts/seed_chroma.py list
python scripts/seed_chroma.py search "高温超导"

# 清理指定来源
python scripts/seed_chroma.py clean --source "physics/xxx.pdf"
```

## 命名规范建议

按 `领域_主题_年份.pdf` 命名，例如：
- `physics_ybco_superconductor_2024.pdf`
- `neuroscience_consciousness_2025.pdf`
- `artificial_intelligence_llm_reasoning_2024.pdf`

优先选择 **综述/Review** 类文献，覆盖领域图谱，检索命中率更高。
