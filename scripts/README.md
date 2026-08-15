# scripts/ - 运维脚本目录

## seed_chroma.py - Chroma 测试数据管理

### 用法

```bash
# 1. 塞入默认测试数据（9 条多学科文献片段）
python scripts/seed_chroma.py seed

# 2. 查看当前向量库中的所有文档
python scripts/seed_chroma.py list

# 3. 测试检索（验证塞进去的数据能被搜到）
python scripts/seed_chroma.py search "如何提升 YBCO 超导转变温度"
python scripts/seed_chroma.py search "神经元临界性" -k 3

# 4. 清空集合（不可恢复）
python scripts/seed_chroma.py clear
python scripts/seed_chroma.py clear -y    # 跳过确认
```

### 添加你自己的数据

打开 [seed_chroma.py](../scripts/seed_chroma.py)，找到 `DEFAULT_SEED_DATA` 列表，按以下格式追加：

```python
{
    "content": "这里是文献正文片段，建议 50-300 字",
    "source": "作者, 年份, 期刊",     # 必填，至少 3 个字符
    "year": "2023",                   # 可选
    "field": "你的学科领域",           # 可选
    "doi": "10.xxxx/xxxxx",          # 可选
},
```

### 重要约束

1. **`content` 不能为空字符串**，否则百炼 Embedding API 会返回 400 错误
2. **`source` 必填且长度 ≥ 3**，否则 Explorer Agent 会拒绝接受这条证据
3. 每条数据塞入时会自动计算 embedding 向量，调用百炼 API（消耗 token）
4. 重复运行 `seed` 命令会追加而不是覆盖，如需重置请先运行 `clear`
