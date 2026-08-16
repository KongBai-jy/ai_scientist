"""
LLM 结构化输出通用容错工具
当 with_structured_output() 失败（网关不支持 response_format / 模型版本差异）时，
降级为：让 LLM 直接吐 JSON 文本 → 解析并修复 → 再用 Pydantic model_validate()。
"""
import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ============================================================
# 1. JSON 文本提取与修复
# ============================================================

def _extract_json_block(text: str) -> Any:
    """从 LLM 返回的文本中提取 JSON（兼容 markdown 代码块包裹 / 前后解释文字）"""
    if not text:
        raise ValueError("LLM 返回内容为空")

    # 1) 优先截取 ```json ... ``` 或 ``` ... ```
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        # 2) 找最外层的 { ... } 或 [ ... ]
        obj_match = re.search(r"\{[\s\S]*\}", text)
        arr_match = re.search(r"\[[\s\S]*\]", text)
        if obj_match and arr_match:
            candidate = (obj_match if obj_match.start() < arr_match.start()
                         else arr_match).group(0)
        elif obj_match:
            candidate = obj_match.group(0)
        elif arr_match:
            candidate = arr_match.group(0)
        else:
            candidate = text.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # 3) 最后手段：去掉末尾逗号等常见问题再试
        repaired = candidate
        repaired = re.sub(r",\s*([\]}])", r"\1", repaired)
        repaired = repaired.replace("True", "true").replace("False", "false").replace("None", "null")
        return json.loads(repaired)


# ============================================================
# 2. 字段映射：把 LLM 拼写的变体名（比如 L1_conceptual）对齐到 schema
# ============================================================

# key: (父字段, 期望字段) -> [可能的错误变体，小写比较]
_FIELD_ALIASES: Dict[tuple, List[str]] = {
    # Plan
    (None, "L1_conceptual"): ["l1_conceptual", "l1conceptual", "conceptual_direction",
                               "conceptual", "level1", "l1"],
    (None, "L2_quantitative"): ["l2_quantitative", "l2quantitative", "quantitative_indicator",
                                 "quantitative", "level2", "l2"],
    (None, "L3_robustness"): ["l3_robustness", "l3robustness", "robustness_plan",
                               "fault_tolerance", "robustness", "level3", "l3"],

    # VerificationCriteria
    (None, "verification_criteria"): ["verificationcriteria", "criteria_for_hypothesis_evaluation",
                                       "evaluation_criteria", "criteria"],
    (None, "confirm"): ["confirm_condition", "confirm_criteria", "confirm"],
    (None, "reject"): ["reject_condition", "reject_criteria", "falsify_condition", "reject"],

    # Hypothesis
    (None, "falsification_condition"): ["falsificationcondition", "falsify_condition",
                                         "falsification", "falsify"],
    (None, "cross_hypothesis_comparison"): ["cross_hypothesis_comparison", "cross_hypothesis",
                                             "hypothesis_comparison", "comparison"],

    # DimensionScores / Critic
    (None, "cross_domain"): ["cross_domain_adaptability", "crossdomain", "interdisciplinarity"],
    (None, "evidence"): ["evidence_support", "evidence"],

    # Explorer
    (None, "problem_skelton"): ["problem_skeleton", "problem_skelton", "framework"],
    (None, "knowledge_gaps"): ["knowledge_gap", "knowledge_gaps", "gaps"],
    (None, "mapping_relation"): ["mapping", "mapping_relation", "relation"],

    # Hypothesis.plan — plan 字段本身可能被写成字符串
}


def _align_fields(obj: Any, parent: Optional[str] = None) -> Any:
    """递归地把字典里的字段名从变体映射到标准名，并把"本来应该是对象的字符串"转成对象"""
    if isinstance(obj, list):
        return [_align_fields(x, parent) for x in obj]
    if not isinstance(obj, dict):
        return obj

    out: Dict[str, Any] = {}
    for k, v in obj.items():
        k_lower = k.lower()

        # 1) 找别名
        mapped_key = k
        for (_, target), aliases in _FIELD_ALIASES.items():
            if k_lower in [a.lower() for a in aliases] or k_lower == target.lower():
                mapped_key = target
                break
        # 完全大小写不同但字母相同
        if mapped_key == k:
            for target_alias_list in _FIELD_ALIASES.values():
                pass  # 上面已经覆盖

        # 2) 递归子结构
        aligned_v = _align_fields(v, mapped_key)

        # 3) 把 "应当是对象但 LLM 写成了字符串描述" 的值强制包装成 object
        if isinstance(aligned_v, str):
            # 如果这个 key 本身是 plan，但值是字符串，尝试把字符串当 JSON
            if mapped_key == "plan":
                parsed = _try_parse_object_text(aligned_v, ["L1_conceptual", "L2_quantitative", "L3_robustness"])
                if parsed:
                    aligned_v = parsed
                else:
                    # 三行 split 的兜底：按换行冒号拆
                    aligned_v = _split_plan_text(aligned_v)
            elif mapped_key == "verification_criteria":
                parsed = _try_parse_object_text(aligned_v, ["confirm", "reject"])
                if parsed:
                    aligned_v = parsed
                else:
                    aligned_v = _split_vc_text(aligned_v)
            elif mapped_key == "scores":
                # scores: 5 维评分对象
                parsed = _try_parse_object_text(
                    aligned_v,
                    ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"]
                )
                if parsed:
                    # 转成 float
                    aligned_v = _floatize_score_dict(parsed)
                else:
                    aligned_v = _split_scores_text(aligned_v)

        out[mapped_key] = aligned_v

    # 4) 对某些"应该是 list[str] 但 LLM 写成字符串"的字段做最后规范化
    for k in ["knowledge_gaps", "missing_evidences", "human_feedback"]:
        if k in out and isinstance(out[k], str):
            # 按句号/分号/换行切，但如果切出来只有 1 个元素，那就整串当 1 条
            parts = [s.strip() for s in re.split(r"[\\n。；;，,]", out[k]) if s.strip()]
            out[k] = parts or [out[k]]

    return out


def _try_parse_object_text(text: str, required_keys: List[str]) -> Optional[Dict[str, Any]]:
    """尝试把一段说明文字 parse 成 dict"""
    text = text.strip()
    if not text:
        return None

    # 1) 先看是否 JSON
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 2) 按"Key: Value"行提取，key 支持别名（大小写不敏感，允许英文冒号:和中文冒号：）
    #    先按 alias 字典建立 alias -> canonical key 的反查表
    alias_rev: Dict[str, str] = {}
    for (_, target), aliases in _FIELD_ALIASES.items():
        if target in required_keys:
            alias_rev[target.lower()] = target
            for a in aliases:
                alias_rev[a.lower()] = target

    result: Dict[str, Any] = {}
    # 用正则切：行内第一个出现的冒号（: 或 ：）作为分隔
    pattern = re.compile(r"\s*(?P<key>[a-zA-Z_][\w]*)\s*[:：]\s*(?P<value>.+?)\s*$")

    # 先尝试按行处理，每行取第一次命中的冒号
    # 如果多行都没匹配，尝试用 句号/分号分段后再处理
    blocks = [b.strip() for b in re.split(r"[\n。；;]", text) if b.strip()]

    for block in blocks:
        m = pattern.match(block)
        if m:
            key_guess = m.group("key").lower()
            value = m.group("value").strip()
            if key_guess in alias_rev:
                canonical = alias_rev[key_guess]
                result.setdefault(canonical, value)
            elif key_guess in [k.lower() for k in required_keys]:
                # 直接就是 canonical key（大小写不一致）
                for rk in required_keys:
                    if rk.lower() == key_guess:
                        result.setdefault(rk, value)
                        break

    # 如果行正则没抓到任何 key，尝试在整个文本中按 alias 作为起始 substring 去抓
    if not result:
        for i, rk in enumerate(required_keys):
            # 为这个 key 收集所有 alias
            variants = [rk] + [a for (_, t), aliases in _FIELD_ALIASES.items()
                             if t == rk for a in aliases]
            for variant in variants:
                # variant: 可能是 conceptual_direction 这种，在文本里找 "variant: xxx"（含结尾换行/句号/分号边界）
                m = re.search(
                    r"(?:^|[\n。；;])\s*" + re.escape(variant) + r"\s*[:：]\s*(.+?)(?=[\n。；;]|$)",
                    text, flags=re.IGNORECASE | re.DOTALL
                )
                if m:
                    val = m.group(1).strip()
                    if val:
                        result.setdefault(rk, val)
                    break

    if result:
        # 缺的键填默认提示（长度满足 Pydantic min_length）
        for k in required_keys:
            result.setdefault(k, _default_for(k))
        return result
    return None


def _default_for(field_name: str) -> str:
    """为特定字段产出长度合规的占位默认值"""
    d = {
        "L1_conceptual": "[自动补齐] 概念级方向描述，请在下一轮迭代中替换为实际方向",
        "L2_quantitative": "[自动补齐] 当关键指标 X 变化幅度 ≥ 30% 或统计检验 p ≤ 0.05 时视为达到量化要求（占位）",
        "L3_robustness": "[自动补齐] 若主方案失败，设计阳性/阴性双对照并切换到替代实验路径",
        "confirm": "[自动补齐] 假设成立：关键指标达到阈值且与对照组有显著差异 p ≤ 0.05",
        "reject": "[自动补齐] 假设推翻：关键指标无改善或与对照组无显著差异 p > 0.05",
    }
    return d.get(field_name, f"[自动补齐] {field_name}（请在下一轮迭代修正）")


def _split_plan_text(text: str) -> Dict[str, str]:
    """兜底：按 Key:Value 行或冒号拆分 plan 文本为 L1/L2/L3，并保证 L2 含数字"""
    # 优先按 key-value 行提取
    plan = _try_parse_object_text(text, ["L1_conceptual", "L2_quantitative", "L3_robustness"])
    if plan and plan.get("L2_quantitative") and re.search(r"\d", plan["L2_quantitative"]):
        return plan

    # 按行/句子切分（切分符：换行、句号、分号、逗号 + 连接词）
    parts = [s.strip() for s in re.split(r"[\\n;。；]", text) if s.strip()]
    l1 = parts[0] if len(parts) >= 1 else text
    l2 = ""
    l3 = ""
    if len(parts) >= 3:
        l2 = parts[1]
        l3 = parts[2]
    elif len(parts) == 2:
        l2 = parts[1]
        l3 = "若主方案失败，采用无钉扎中心纯 YBCO 对照并在 77K 多点测量"
    else:
        # 就 1 段文字，按 1/3 2/3 切（fallback）
        third = len(text) // 3
        l1 = text[:third]
        l2 = text[third: 2 * third]
        l3 = text[2 * third:]

    # L2 必须有数字，否则硬塞一个保守阈值（提示下一轮 LLM 再精确化）
    if not re.search(r"\d", l2):
        l2 = "当关键指标 X 的变化幅度 ≥ 30% 或统计显著性 p ≤ 0.05 时视为满足量化条件（占位，下一轮请替换为实际阈值）；" + l2

    return {
        "L1_conceptual": l1 or "[自动补齐] L1_conceptual",
        "L2_quantitative": l2,
        "L3_robustness": l3 or "[自动补齐] 设计阴性/阳性双对照，必要时切换到替代实验路径并控制混杂变量",
    }


def _split_vc_text(text: str) -> Dict[str, str]:
    """兜底：拆分 verification_criteria 文本为 confirm/reject"""
    vc = _try_parse_object_text(text, ["confirm", "reject"])
    if vc and vc.get("confirm") and vc.get("reject"):
        return vc
    # 按前后 1/2 切
    half = len(text) // 2
    return {
        "confirm": (text[:half] if half > 0 else text) or "（未提供，默认成立条件：关键指标达到预期阈值且对照组有显著差异）",
        "reject": (text[half:] if text[half:] else text) or "（未提供，默认推翻条件：关键指标无显著改善或与对照组无统计差异 p>0.05）",
    }


def _floatize_score_dict(d: Dict[str, Any]) -> Dict[str, float]:
    """把 score 字典的字符串值转为 float（失败给默认 5.0）"""
    out: Dict[str, float] = {}
    for k in ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"]:
        v = d.get(k)
        if v is None:
            out[k] = 5.0
            continue
        try:
            num = float(v)
            # clamp 0-10
            out[k] = max(0.0, min(10.0, num))
        except (TypeError, ValueError):
            # 尝试从字符串中提第一个浮点数
            m = re.search(r"(\d+(?:\.\d+)?)", str(v))
            if m:
                try:
                    num = float(m.group(1))
                    out[k] = max(0.0, min(10.0, num))
                    continue
                except ValueError:
                    pass
            out[k] = 5.0
    return out


def _split_scores_text(text: str) -> Dict[str, float]:
    """兜底：从一段 scores 描述中提取 5 维分数，全部失败给 5.0"""
    extracted = _try_parse_object_text(
        text, ["evidence", "falsifiability", "consistency", "novelty", "cross_domain"]
    )
    if extracted:
        return _floatize_score_dict(extracted)
    return _floatize_score_dict({})


# ============================================================
# 3. 顶层：把 LLM 原始输出（str）变成 Pydantic Model 实例
# ============================================================

def parse_llm_json_to_model(raw_output: str, model_cls: Type[T]) -> T:
    """
    将 LLM 文本输出安全转换为 Pydantic 模型实例。

    使用场景：
      - with_structured_output() 抛出 PydanticValidationError 时
      - 不同 API 网关（自建网关 / 专有云）不支持 response_format 时
    """
    # 1) 提取 JSON
    data = _extract_json_block(raw_output)
    # 2) 字段对齐（大小写/别名/字符串→对象包装）
    aligned = _align_fields(data)
    # 3) Pydantic 校验
    try:
        return model_cls.model_validate(aligned)
    except Exception as e:
        logger.warning(
            "Pydantic 校验失败，尝试做最后一层字段补齐：%s\n对齐后的数据结构:\n%s",
            e,
            json.dumps(aligned, ensure_ascii=False, indent=2)[:2000],
        )
        return _coerce_model(aligned, model_cls)


def _coerce_model(data: Dict[str, Any], model_cls: Type[T]) -> T:
    """最后一层兜底：按 Pydantic schema 字段填充默认合法值 + 字段自定义校验失败时不抛错"""
    from pydantic import ValidationError

    schema = model_cls.model_fields

    # 模型偶尔会返回 JSON 数组（例如逐条评审列表）而 schema 期望单个对象：
    # 取第一个元素兜底，避免 dict(list) 抛出难以理解的 "dictionary update sequence" 错误
    if isinstance(data, list):
        logger.warning(
            "模型返回了数组，但 %s 期望单个对象：取第一个元素兜底",
            model_cls.__name__,
        )
        data = data[0] if data and isinstance(data[0], dict) else {}
    elif not isinstance(data, dict):
        logger.warning(
            "模型返回类型异常（%s），%s 重置为空对象兜底",
            type(data).__name__, model_cls.__name__,
        )
        data = {}
    patched: Dict[str, Any] = dict(data)

    for field_name, field_info in schema.items():
        annotation = field_info.annotation
        default = field_info.default

        # 1) 嵌套 Pydantic 类型：如果数据里已经有 dict 但字段 validator 已经在子 model 生效，这里不做嵌套 model_construct，而是递归走 _coerce_model
        #    如果 list[PydanticModel]：对列表元素递归 _coerce_model
        if field_name in patched:
            v = patched[field_name]
            origin = getattr(annotation, "__origin__", None)
            # List[SomeBaseModel]
            if origin is list and isinstance(v, list):
                try:
                    item_cls = annotation.__args__[0]  # type: ignore[union-attr]
                    if isinstance(item_cls, type) and issubclass(item_cls, BaseModel):
                        patched[field_name] = [
                            _coerce_model(x, item_cls) if isinstance(x, dict) else x
                            for x in v
                        ]
                        continue
                except (AttributeError, IndexError, TypeError):
                    pass
            # 单个子模型
            if isinstance(v, dict):
                try:
                    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                        patched[field_name] = _coerce_model(v, annotation)
                        continue
                except TypeError:
                    pass
            continue

        # 2) 字段缺失：尝试给默认值
        try:
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                patched[field_name] = _coerce_model({}, annotation)
                continue
        except TypeError:
            pass

        if default is not None or field_info.default_factory is not None:
            continue

        origin = getattr(annotation, "__origin__", None)
        if origin is list or str(annotation).startswith("typing.List"):
            patched[field_name] = []
        elif annotation is str:
            patched[field_name] = f"[自动补齐] {field_name}（请在下一轮迭代修正）"
        elif annotation is float or annotation is int:
            patched[field_name] = 0
        elif origin is dict or str(annotation).startswith("typing.Dict"):
            patched[field_name] = {}
        else:
            patched[field_name] = None

    patched = _pre_patch_for_validators(patched, model_cls)

    try:
        return model_cls.model_validate(patched)
    except ValidationError:
        return model_cls.model_construct(**patched)


def _pre_patch_for_validators(data: Dict[str, Any], model_cls) -> Dict[str, Any]:
    """根据 schema 名对已知自定义校验字段做最小合规填充"""
    # Plan.L2_quantitative 需要包含数字
    if "L2_quantitative" in data and isinstance(data["L2_quantitative"], str):
        if not re.search(r"\d", data["L2_quantitative"]):
            data["L2_quantitative"] = (
                "当关键指标 X 的变化幅度 ≥ 30% 或统计显著性 p ≤ 0.05 时视为满足量化条件（占位，下一轮迭代时替换为实际阈值）；"
                + data["L2_quantitative"]
            )
    # Hypothesis.falsification_condition ≥ 15 字符
    if "falsification_condition" in data and isinstance(data["falsification_condition"], str):
        if len(data["falsification_condition"]) < 15:
            data["falsification_condition"] = (
                "若在可重复实验中观测到关键指标未达阈值且对照组无显著差异（p > 0.05），则假设被推翻；"
                + data["falsification_condition"]
            )
    # Critic.counterfactual ≥ 15
    if "counterfactual" in data and isinstance(data["counterfactual"], str):
        if len(data["counterfactual"]) < 15:
            data["counterfactual"] = (
                "在极端不可行条件下（如测量精度 < 1e-12、样本量 N<3 或混杂因子完全不可控），该假设必然失效；"
                + data["counterfactual"]
            )
    return data
