"""统一 LLM 调用接口（OpenAI 兼容 + 离线 Mock）。

平移要点：内网换成 vLLM / Xinference 部署的本地模型时，
只需改 config/llm.yaml 里的 base_url 与 model，代码一行不用动。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from abc import ABC, abstractmethod
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .config import ROOT, LLMConfig


class UsageRecord:
    """记录每次调用的 token 消耗，用于后续规模化成本估算。"""

    def __init__(self, path: str = "outputs/token_usage.jsonl"):
        self.path = ROOT / path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, **kw):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(kw, ensure_ascii=False) + "\n")


class BaseLLM(ABC):
    @abstractmethod
    def complete(self, prompt: str, *, task: str = "", key: str = "") -> str:
        """返回模型原始文本输出（调用方负责 JSON 解析）。"""


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    return m.group(1).strip() if m else t


def parse_json_array(text: str) -> list:
    """宽容解析：去围栏 → 直接 loads → 截取首个 JSON 数组 → 逐对象抢救。"""
    t = _strip_code_fence(text)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        pass
    # 尝试提取第一个完整的 JSON 数组片段
    start = t.find("[")
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "[":
            depth += 1
        elif t[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start : i + 1])
                except json.JSONDecodeError:
                    break
    # 最后手段：逐个 {...} 抢救
    objs = []
    depth = 0
    buf = ""
    for ch in t:
        if ch == "{":
            depth += 1
            buf = ch if depth == 1 else buf + ch
        elif depth > 0:
            buf += ch
            if ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        objs.append(json.loads(buf))
                    except json.JSONDecodeError:
                        pass
                    buf = ""
    return objs


class OpenAICompatibleLLM(BaseLLM):
    """OpenAI 兼容端点（DeepSeek / GPT-4o / vLLM / Xinference 均适用）。"""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg
        self.model = cfg.cfg.get("model")
        self.base_url = cfg.cfg.get("base_url", "").rstrip("/")
        self.temperature = cfg.cfg.get("temperature", 0.0)
        self.max_tokens = cfg.cfg.get("max_tokens", 4096)
        self.retry = int(cfg.cfg.get("retry", 3))
        self.backoff = float(cfg.cfg.get("retry_backoff_sec", 2.0))
        self.usage = UsageRecord()
        key_env = cfg.cfg.get("api_key_env")
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError(
                    "真实模式需要 openai 包：pip install openai；或改用 --provider mock 离线演示"
                ) from e
            self._client = OpenAI(api_key=self.api_key or "EMPTY", base_url=self.base_url)
        return self._client

    def complete(self, prompt: str, *, task: str = "", key: str = "") -> str:
        client = self._ensure_client()
        last_err = None
        for attempt in range(self.retry):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens,
                }
                if self.cfg.cfg.get("response_format") == "json_object":
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**kwargs)
                usage = getattr(resp, "usage", None)
                if self.cfg.cfg.get("record_token_usage", True) and usage is not None:
                    self.usage.log(
                        task=task, key=key, model=self.model,
                        prompt_tokens=getattr(usage, "prompt_tokens", None),
                        completion_tokens=getattr(usage, "completion_tokens", None),
                    )
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(self.backoff * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败（已重试 {self.retry} 次）：{last_err}")


class MockLLM(BaseLLM):
    """离线演示用：读取预置抽取结果，按 key 返回，不需要任何 API key。

    这样 pipeline 的解析→后处理→评估→入图全链路都可以在无网环境下跑通和验证，
    接真实 LLM 时把 --provider 换成 deepseek/openai/vllm_qwen 即可。
    """

    def __init__(self, cfg: LLMConfig):
        cfg.cfg = cfg.cfg or {}
        fixture_rel = cfg.cfg.get("fixture_path", "data/fixtures/mock_extractions.json")
        fixture = ROOT / fixture_rel
        if not fixture.exists():
            raise FileNotFoundError(f"Mock fixture 不存在：{fixture}")
        with open(fixture, "r", encoding="utf-8") as f:
            self.data: dict = json.load(f)
        self.usage = UsageRecord()
        self._alias: dict | None = None
        self._corpus: str | None = None

    # ---------- NLI 的本地替身：只做"证据覆盖率"检查，替代大模型判定 ----------
    def _load_alias(self) -> dict:
        if self._alias is None:
            alias = {}
            g = ROOT / "data/terminology/glossary.csv"
            if g.exists():
                with io.open(g, encoding="utf-8-sig") as f:
                    for row in csv.DictReader(f):
                        for a in (row.get("aliases") or "").split("|"):
                            if a.strip():
                                alias[a.strip()] = row["canonical"].strip()
            # 别名替换按长度降序，避免短别名先被吃掉
            self._alias = dict(sorted(alias.items(), key=lambda kv: -len(kv[0])))
        return self._alias

    def _canon_text(self, text: str) -> str:
        out = text
        for surv, canon in self._load_alias().items():
            out = out.replace(surv, canon)
        return out

    def _corpus_text(self) -> str:
        if self._corpus is None:
            parts = []
            for p in sorted((ROOT / "data/sample").glob("*.md")):
                parts.append(p.read_text(encoding="utf-8"))
            self._corpus = "\n".join(parts)
        return self._corpus

    @staticmethod
    def _clean(s: str) -> str:
        """去掉括号内容与所有空白/标点，便于做包含判断（如"蒸汽发生器（SG）" ≡ "蒸汽发生器"）。"""
        s = re.sub(r"[（(][^（()）]*[）)]", "", s)
        s = re.sub(r"[\s　，,。；;：:、\-—_/]", "", s)
        return s

    @classmethod
    def _fuzzy_in(cls, needle: str, haystack: str, threshold: float = 0.72) -> bool:
        """宽松包含判断：先精确包含，再退化为滑动窗口的最长相似串。"""
        n, h = cls._clean(needle), cls._clean(haystack)
        if not n or not h:
            return False
        if n in h:
            return True
        if len(n) > len(h):
            return False
        best = 0.0
        step = max(1, len(n) // 3)
        for i in range(0, len(h) - len(n) + 1, step):
            r = SequenceMatcher(None, n, h[i : i + len(n)]).ratio()
            best = max(best, r)
        return best >= threshold

    @staticmethod
    def _field(prompt: str, label: str) -> str:
        """从 prompt 中按行首标签取值，如 '陈述：xxx' / '证据：xxx'。"""
        for line in prompt.splitlines():
            if line.startswith(label):
                return line[len(label):].strip()
        return ""

    def _parse_statement(self, stmt: str) -> tuple[str, str, str]:
        """陈述格式："主语 ——[关系]——> 宾语" """
        m = re.match(r"^(.*?)\s*——\[(.*?)\]——>\s*(.*)$", stmt)
        if m:
            return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        return stmt.strip(), "", ""

    def _local_nli(self, prompt: str) -> dict:
        subj, pred, obj = self._parse_statement(self._field(prompt, "陈述："))
        ev = self._field(prompt, "证据：")
        if not ev:
            return {"verdict": "NOT_SUPPORTED", "reason": "未提供证据片段", "suggestion": None}
        if ev not in self._corpus_text():
            return {"verdict": "NOT_SUPPORTED", "reason": "证据未出现在原文语料中，疑为改写或幻觉",
                    "suggestion": None}
        # STEP_OF 特例：整篇文档就是该规程，只需证据里出现该动作 + 明确的步骤标记
        if pred == "STEP_OF" and self._fuzzy_in(subj, ev) and ("步骤" in ev or "操作内容" in ev):
            return {"verdict": "SUPPORTED", "reason": "证据含步骤标记且覆盖动作实体", "suggestion": None}
        missing = [x for x in (subj, obj) if x and not self._fuzzy_in(x, self._canon_text(ev))
                   and not self._fuzzy_in(self._canon_text(x), self._canon_text(ev))]
        if missing:
            return {"verdict": "NOT_SUPPORTED", "reason": f"证据中未出现要素：{'、'.join(missing)}",
                    "suggestion": None}
        return {"verdict": "SUPPORTED", "reason": "证据覆盖了陈述的全部要素", "suggestion": None}

    def complete(self, prompt: str, *, task: str = "", key: str = "") -> str:
        if task == "nli":
            verdict = self.data.get("nli", {}).get(key)
            if verdict is None:
                verdict = self._local_nli(prompt)
            self.usage.log(task=task, key=key, model="mock-v0",
                           prompt_tokens=len(prompt) // 2, completion_tokens=0)
            return json.dumps(verdict, ensure_ascii=False)
        bucket = self.data.get(task, {})
        payload = bucket.get(key, [])
        self.usage.log(task=task, key=key, model="mock-v0",
                       prompt_tokens=len(prompt) // 2, completion_tokens=0)
        return json.dumps(payload, ensure_ascii=False)


def build_llm(cfg: LLMConfig) -> BaseLLM:
    kind = cfg.cfg.get("type", "mock")
    if kind == "mock":
        return MockLLM(cfg)
    return OpenAICompatibleLLM(cfg)
