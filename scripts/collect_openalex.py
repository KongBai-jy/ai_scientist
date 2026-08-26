"""
OpenAlex 离线预采集 CLI
======================

一次性把 data/sci2025_problems.json 里的 125 个科学问题，从 OpenAlex
拉取对应的论文元数据（标题 / 作者 / 年份 / DOI / 摘要 / 引用数 / 来源 / 关键词），
按 12 个学科主题落盘到 papers/<category_en>/openalex/<id>_<slug>.json，
作为后续离线入向量库 / 本地知识库的原料。

特性:
  * 免 Key：OpenAlex 公开 API，无需 token
  * 礼貌池：读取 .env 里的 OPENALEX_EMAIL 作为 mailto query 参数（提升 QPS 与稳定性）
  * 每个问题同时用中文 + 英文各搜一次，按 openalex_id 去重合并
  * 摘要从 abstract_inverted_index 倒排索引还原为纯文本
  * httpx 网络层重试 + 应用层 429/5xx 退避 + 每请求 1s 间隔

用法:
    # 干跑：每个学科只采第一个问题（12 个样例），验证格式与落盘
    python scripts/collect_openalex.py --dry-run

    # 全量：125 个问题（每个问题 top-10）
    python scripts/collect_openalex.py

    # 自定义每问题篇数
    python scripts/collect_openalex.py --per-page 5
"""

import os
import re
import json
import time
import argparse
from pathlib import Path

import httpx
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

# 12 个中文领域 -> papers/ 下的英文文件夹名
CATEGORY_FOLDER = {
    "数学科学": "mathematical_sciences",
    "化学": "chemistry",
    "医学与健康": "medicine_health",
    "生物学": "biology",
    "天文学": "astronomy",
    "物理学": "physics",
    "工程与材料科学": "engineering_materials",
    "信息科学": "information_science",
    "神经科学": "neuroscience",
    "生态学": "ecology",
    "能源科学": "energy_science",
    "人工智能": "artificial_intelligence",
}

OPENALEX_BASE = "https://api.openalex.org/works"
DEFAULT_PER_PAGE = 5
DEFAULT_MAX_PER_QUESTION = 5
REQUEST_DELAY_SEC = 2.0
MAX_RETRIES = 3
RATE_LIMIT_COOLDOWN_SEC = 30.0  # 429 限流后的冷却时长（无 Retry-After 时的兜底）


def _load_email() -> str:
    """读取 OpenAlex 礼貌池邮箱；为空则返回空串（脚本降级为不带 mailto 并提示）。"""
    return (os.getenv("OPENALEX_EMAIL") or "").strip()


def _load_problems(path: Path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for i, p in enumerate(data, 1):
        p["id"] = i  # 附加 1-based 序号，与 sci2025_problems.json 顺序一致
    return data


def _build_client() -> httpx.Client:
    transport = httpx.HTTPTransport(retries=1)
    return httpx.Client(
        timeout=30.0,
        transport=transport,
        headers={"User-Agent": "AI-Scientist/1.0 (OpenAlex offline collector)"},
    )


def _clean_query(s: str) -> str:
    """去掉末尾的中英文问号 / 句号，避免干扰检索。"""
    if not s:
        return s
    return re.sub(r"[\s?？。．.!！]+$", "", s.strip()) or s.strip()


def _search(client: httpx.Client, query: str, email: str, per_page: int):
    """带退避的 OpenAlex works 检索，返回 (results, total) 或 (None, 0)。"""
    params = {
        "search": query,
        "per_page": per_page,
        "sort": "relevance_score:desc",
    }
    if email:
        params["mailto"] = email

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(OPENALEX_BASE, params=params)
            if resp.status_code == 429:
                # 触发限流：长冷却（优先遵守 Retry-After，缺省用 30s 兜底）
                retry_after = resp.headers.get("Retry-After")
                try:
                    cool = min(float(retry_after), 120.0) if retry_after and retry_after.strip() else RATE_LIMIT_COOLDOWN_SEC
                except Exception:
                    cool = RATE_LIMIT_COOLDOWN_SEC
                print(
                    f"    [warn] OpenAlex 429 限流，冷却 {cool:.0f}s 后重试 ({attempt}/{MAX_RETRIES})"
                )
                time.sleep(cool)
                continue
            if resp.status_code >= 500:
                backoff = 2 * attempt
                print(
                    f"    [warn] OpenAlex 返回 {resp.status_code}，"
                    f"{backoff}s 后重试 ({attempt}/{MAX_RETRIES})"
                )
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", []), data.get("meta", {}).get("count", 0)
        except httpx.HTTPError as e:
            print(f"    [warn] OpenAlex 请求异常 ({attempt}/{MAX_RETRIES}): {type(e).__name__}")
            time.sleep(2 * attempt)
        except Exception as e:
            print(f"    [warn] 未知异常 ({attempt}/{MAX_RETRIES}): {type(e).__name__}: {e}")
            time.sleep(2 * attempt)
    return None, 0


def _reconstruct_abstract(inverted_index) -> str:
    """把 OpenAlex 的 abstract_inverted_index（{词: [位置, ...]}）还原为纯文本。

    算法：flatten 成 [(pos, word)] -> 按 pos 升序 -> join 空格。
    处理 inverted_index 为 None / 空 dict / 位置列表为空的情况。
    """
    if not inverted_index:
        return ""
    positioned = []
    for word, positions in inverted_index.items():
        if not positions:
            continue
        for pos in positions:
            positioned.append((pos, word))
    if not positioned:
        return ""
    positioned.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positioned)


def _parse_work(work: dict) -> dict:
    """把单个 OpenAlex work 转成精简元数据。"""
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return {
        "openalex_id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("display_name") or "",
        "year": work.get("publication_year"),
        "cited_by_count": work.get("cited_by_count"),
        "relevance_score": work.get("relevance_score"),
        "authors": [
            (a.get("author") or {}).get("display_name")
            for a in work.get("authorships", [])
            if (a.get("author") or {}).get("display_name")
        ],
        "venue": source.get("display_name"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "keywords": [c.get("display_name") for c in work.get("concepts", [])[:5]],
        "url": work.get("id"),
    }


def _slugify(text: str, max_len: int = 48) -> str:
    """把英文标题压缩成文件名安全的 slug。"""
    if not text:
        return "question"
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return slug[:max_len] or "question"


def _already_collected(out_file: Path) -> bool:
    """判断某问题是否已采到 >0 篇（供 --resume 跳过）。文件缺失或 paper_count==0 都视为需补采。"""
    if not out_file.exists():
        return False
    try:
        data = json.loads(out_file.read_text(encoding="utf-8"))
        return int(data.get("paper_count", 0)) > 0
    except Exception:
        return False


def _collect_question(client, email, problem, per_page, max_per_question=5):
    """对单个问题，用 en + cn 各搜一次、按 openalex_id 去重合并，仅保留前 max_per_question 篇。

    顺序：en 结果优先入序、cn 结果补充（不重复的），因此保留的是"en 相关度最高的候选 +
    若不足再由 cn 补足"的前 N 篇。
    """
    en_q = _clean_query(problem.get("en", ""))
    cn_q = _clean_query(problem.get("cn", ""))

    merged = {}  # openalex_id -> work dict（en 优先，cn 补充）
    for label, q in (("en", en_q), ("cn", cn_q)):
        if not q:
            continue
        results, total = _search(client, q, email, per_page)
        if results is None:
            print(f"    [warn] query 失败: {q!r}")
            continue
        print(f"    query[{label}] {q!r} -> total={total}, got={len(results)}")
        for w in results:
            oid = w.get("id")
            if not oid:
                continue
            if oid not in merged:
                parsed = _parse_work(w)
                parsed["matched_by"] = label
                merged[oid] = parsed

    # en+cn 合并去重后只保留前 max_per_question 篇
    papers = list(merged.values())[:max_per_question]
    return papers


def main():
    parser = argparse.ArgumentParser(description="OpenAlex 离线预采集 125 个科学问题")
    parser.add_argument("--dry-run", action="store_true",
                        help="每个学科只采第一个问题（12 个样例）")
    parser.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE,
                        help=f"每个问题每次检索（en/cn 各一次）从 API 拉取的候选篇数（默认 {DEFAULT_PER_PAGE}）")
    parser.add_argument("--max-per-question", type=int, default=DEFAULT_MAX_PER_QUESTION,
                        help=f"en+cn 合并去重后每个问题最终保留的论文篇数（默认 {DEFAULT_MAX_PER_QUESTION}）")
    parser.add_argument("--problems",
                        default=str(_PROJECT_ROOT / "data" / "sci2025_problems.json"),
                        help="问题数据源 JSON 路径")
    parser.add_argument("--papers-dir", default=str(_PROJECT_ROOT / "papers"),
                        help="输出根目录（默认项目 papers/）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续跑：跳过已采集到 >0 篇的问题，只补采缺失或保存 0 篇的问题。"
                             "需终端先冷却（避免再次 429）后再跑。")
    args = parser.parse_args()

    email = _load_email()
    if not email:
        print("[提示] 未配置 OPENALEX_EMAIL，将不带 mailto 参数（默认约 1 req/s，可能更易限流）。")
        print("       建议在 .env 中加一行 OPENALEX_EMAIL=你的邮箱 以进入 OpenAlex 礼貌池。\n")

    problems = _load_problems(Path(args.problems))
    papers_dir = Path(args.papers_dir)

    if args.dry_run:
        seen = set()
        targets = []
        for p in problems:
            if p["category"] not in seen:
                seen.add(p["category"])
                targets.append(p)
        print(f"[dry-run] 从 {len(problems)} 个问题中抽取每学科第 1 个，共 {len(targets)} 个样例\n")
    else:
        targets = problems
        print(f"[full] 共 {len(targets)} 个问题，每问题合并去重后保留 top-{args.max_per_question}\n")

    client = _build_client()
    total_papers = 0
    try:
        for idx, p in enumerate(targets, 1):
            cat_en = CATEGORY_FOLDER.get(p["category"], "uncategorized")
            out_dir = papers_dir / cat_en / "openalex"
            out_dir.mkdir(parents=True, exist_ok=True)

            pid = p.get("id", idx)
            slug = _slugify(p.get("en", ""))
            out_file = out_dir / f"{pid:03d}_{slug}.json"

            # 断点续跑：跳过已采到 >0 篇的问题
            if args.resume and _already_collected(out_file):
                print(f"[{idx}/{len(targets)}] #{pid:03d} 已有数据，跳过（--resume）")
                continue

            print(f"[{idx}/{len(targets)}] #{pid:03d} [{p['category']}] {p['cn']}")
            papers = _collect_question(client, email, p, args.per_page, args.max_per_question)
            total_papers += len(papers)

            payload = {
                "problem_id": pid,
                "problem_cn": p.get("cn", ""),
                "problem_en": p.get("en", ""),
                "category": p.get("category", ""),
                "query_en": _clean_query(p.get("en", "")),
                "query_cn": _clean_query(p.get("cn", "")),
                "source": "openalex",
                "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "paper_count": len(papers),
                "papers": papers,
            }
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"    -> 保存 {len(papers)} 篇到 {out_file.relative_to(_PROJECT_ROOT)}")

            # 每次请求之间留 1s，避免撞限流
            time.sleep(REQUEST_DELAY_SEC)
    finally:
        client.close()

    print(f"\n完成。共处理 {len(targets)} 个问题，采集 {total_papers} 篇论文元数据。")
    print(f"输出根目录: {papers_dir.resolve()}")


if __name__ == "__main__":
    main()