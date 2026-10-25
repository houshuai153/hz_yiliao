"""DeepSeek（OpenAI 兼容 chat/completions）provider。

安全边界：
- 发出去的文本先经 redact 脱敏；返回的文本再脱敏一次；
- 数字、编号、引用标记、“[待补充: …]”都做白名单 / 保留校验，校验失败一律回退到原文或“不支持”，不放行；
- 判定（pass/fail/pending_review）、指标、事实数据不交给模型，模型也不参与计算；
- 密钥只存在于 Settings.llm_api_key（SecretStr），不进日志、不进异常信息、不进审计；
- 不跟随重定向（避免把 Authorization 头带到别的主机）；任何网络 / 解析错误都降级，不抛给调用方。
"""
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import requests

from app.llm.redact import redact
from app.settings import Settings
from app.translation.glossary import Glossary
from app.translation.patterns import PatternResult, PatternTranslator

_NUM = re.compile(r"\d+(?:\.\d+)?")
_IDS = re.compile(r"DEMO-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}|PRJ-[A-Z0-9]+(?:-[A-Z0-9]+)+|F\d+")
_MARK = re.compile(r"【[^】]*】")
_FENCE = re.compile(r"^```[a-z]*\n?|\n?```$")


def _nums(text: str) -> list[str]:
    """正文里的数字（先去掉编号和【…】标记，避免把 DEMO-SOP-DEV-004 里的 004 当成数字）。"""
    return _NUM.findall(_IDS.sub("", _MARK.sub("", text)))


UNTRANSLATABLE = "UNTRANSLATABLE"
PROVIDER_TAG = "LLM:deepseek"


class LLMError(Exception):
    """调用失败（仅含状态类信息，不含请求 / 响应正文，更不含密钥）。"""


@dataclass
class Rewrite:
    text: str                       # 最终文本（失败时为原文）
    used: bool                      # 是否采用了模型输出
    reason: str | None = None       # 未采用的原因
    redactions: dict = field(default_factory=dict)   # 发送前脱敏命中


class DeepSeekProvider:
    name = "deepseek"
    is_generative = True

    def __init__(self, settings: Settings, glossary: Glossary | None = None, session: requests.Session | None = None):
        self._key = settings.llm_api_key
        self.model, self.timeout = settings.llm_model, settings.llm_timeout_s
        self.url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        self.session = session or requests.Session()
        self.glossary = glossary or Glossary.load()
        self.patterns = PatternTranslator.load(self.glossary)

    # ---------- 传输 ----------
    def _chat(self, system: str, user: str, max_tokens: int = 1024) -> str:
        body = {"model": self.model, "temperature": 0, "max_tokens": max_tokens, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        try:
            r = self.session.post(self.url, json=body, timeout=self.timeout, allow_redirects=False,
                                  headers={"Authorization": f"Bearer {self._key.get_secret_value()}"})
        except requests.RequestException as exc:
            raise LLMError(f"网络错误：{exc.__class__.__name__}") from None
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}")
        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("响应格式异常") from None
        if not isinstance(content, str):
            raise LLMError("响应格式异常")
        return _FENCE.sub("", content.strip()).strip()

    # ---------- 翻译：固定句型优先，句型之外才问模型 ----------
    def translate_sentence(self, sentence: str, direction: str) -> PatternResult | None:
        hit = self.patterns.translate(sentence, direction)
        if hit is not None:
            return hit
        src_lang, tgt_lang = ("中文", "英文") if direction == "zh2en" else ("英文", "中文")
        terms = []
        for h in self.glossary.find_hits(sentence):
            t = self.glossary.by_id[h.term_id]
            terms.append(f"{t.zh} = {t.en}" + (f"（缩写 {t.abbr}）" if t.abbr else ""))
        clean, _ = redact(sentence)
        system = (f"你是生物分析 CRO 的专业翻译。把{src_lang}句子翻译成{tgt_lang}。只输出译文，不要解释。"
                  f"所有数字、单位、编号、缩写保持原样；术语必须使用给定的对照。无法忠实翻译时只输出 {UNTRANSLATABLE}。")
        user = ("术语对照：\n" + "\n".join(terms) + "\n\n" if terms else "") + f"句子：{clean}"
        try:
            out = self._chat(system, user, 512)
        except LLMError:
            return None
        out, _ = redact(out)
        if not out or out == UNTRANSLATABLE or "\n" in out or len(out) > 4 * len(sentence) + 40:
            return None
        if set(_nums(out)) != set(_nums(clean)):   # 数字必须与原文完全一致，不增不减
            return None
        return PatternResult(out, PROVIDER_TAG)

    # ---------- 润色 / 改写（统一校验）----------
    def rewrite(self, text: str, *, instruction: str, allowed_numbers: set[str], must_keep: Iterable[str] = (),
                allow_digits: bool = True) -> Rewrite:
        clean, red = redact(text)
        system = ("你是生物分析 CRO 的文档编辑，只做语言上的整理，不得增删或改变事实。"
                  "数字、编号、【…】引用标记、[F…] 标签、“[待补充: …]”必须原样保留，不得新增任何数字或编号。只输出正文。")
        try:
            out = self._chat(system, f"{instruction}\n\n原文：\n{clean}")
        except LLMError as exc:
            return Rewrite(text, False, f"llm_error:{exc}", red)
        out, _ = redact(out)
        problem = self._check_rewrite(text, out, allowed_numbers, list(must_keep), allow_digits)
        return Rewrite(text, False, problem, red) if problem else Rewrite(out, True, None, red)

    @staticmethod
    def _check_rewrite(src: str, out: str, allowed: set[str], must_keep: list[str], allow_digits: bool) -> str | None:
        if not out:
            return "empty_output"
        if len(out) > max(2 * len(src), 120):
            return "too_long"
        if sorted(_MARK.findall(out)) != sorted(_MARK.findall(src)):
            return "citation_markers_changed"
        if not set(_IDS.findall(out)) <= set(_IDS.findall(src)):
            return "new_identifiers"
        if not allow_digits and _nums(out):
            return "digits_not_allowed"
        if not set(_nums(out)) <= allowed:
            return "new_numbers"
        if any(k not in out for k in must_keep):
            return "required_text_missing"
        return None

    def polish(self, text: str, allowed_numbers: set[str]) -> str:
        return self.rewrite(text, instruction="把下面的要点整理成通顺、简洁的中文回答，保持每条要点后的引用标记不变。",
                            allowed_numbers=allowed_numbers).text
