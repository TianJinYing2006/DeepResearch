"""审计 P1#1：人物身份核验（查询实体 vs 证据文本）。

背景：向量检索 Top-K 不判断「材料是否对应目标人物」，无关人物资料（低分）也会
进入证据并可能被模型错误归因（staging 实测：问「田金应」返回「田金洲」）。

本模块先做**第一道闸**（阻止完全无关人物进入证据）：

- :func:`extract_entities`：从查询中抽取人物/实体名候选（标记词法：是谁/多大/年龄/
  出生/简历/简介/资料/的个人/联系方式等；引号包裹的名称直接采信）；
- :func:`hit_matches_entities`：查询含实体时，证据文本必须包含该实体。

同名消歧（学校/公司等多线索佐证）留作后续增强；此处只做「名字必须出现」的硬闸，
配合无答案路径（证据被清空 → 报告明确「信息不足」，而不是拿别人资料回答）。
"""
from __future__ import annotations

import re
from typing import List, Sequence

#: 人物查询标记词（名字紧邻其前）
_MARKERS = (
    r"(?:是谁|多大|几岁|年龄|出生|生日|简历|简介|个人资料|的个人|的联系|"
    r"邮箱|电话|联系方式|工作经历|教育背景|的背景|的经历)"
)
_CJK_RUN = re.compile(r"([\u4e00-\u9fff]{2,8})" + _MARKERS)
_QUOTED = re.compile(r"[「『\"'“”]([^」』\"'“”]{2,20})[」』\"'“”]")

#: 名字与标记词之间的修饰语（如「田金应**现在**多大」）
_TRAILING_MODIFIERS = ("现在", "今年", "目前", "如今", "到底", "究竟", "平时", "一般", "大概")

#: 代词/泛称开头不作为人名（避免「我是」「我的」「这个」被当实体）
_PRONOUN_PREFIX = ("我", "你", "他", "她", "它", "们", "这", "那", "哪", "谁")
_STOPWORDS = {"自己", "本人", "我们", "这个", "那个", "什么", "资料"}


def extract_entities(query: str) -> List[str]:
    """抽取查询中的实体名候选（保持出现顺序、去重）。"""
    found: List[str] = []

    def _add(name: str) -> None:
        name = name.strip()
        for modifier in _TRAILING_MODIFIERS:
            if name.endswith(modifier) and len(name) > len(modifier):
                name = name[: -len(modifier)]
        if not name or not (2 <= len(name) <= 4):
            return
        if name in _STOPWORDS or name in found:
            return
        if name[0] in _PRONOUN_PREFIX:
            return
        found.append(name)

    for match in _CJK_RUN.finditer(query):
        _add(match.group(1))
    for match in _QUOTED.finditer(query):
        _add(match.group(1))
    return found


def hit_matches_entities(text: str, entities: Sequence[str]) -> bool:
    """证据是否包含查询实体；无实体（普通查询）时不过滤。"""
    if not entities:
        return True
    return any(entity in text for entity in entities)
