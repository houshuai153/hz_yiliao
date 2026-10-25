"""LLMProvider 接口：为将来接入本地模型预留。

当前只有 DeterministicProvider：规则 + 模板实现，`is_generative = False`，不具备任何生成式 AI 能力。
默认离线。在线 provider（deepseek）必须由使用者在 .env 显式设置 LLM_PROVIDER 与 LLM_API_KEY（见 Settings）；
任何失败都降级为确定性结果，不得静默改走其它服务。
"""
from typing import Protocol

from app.settings import Settings
from app.translation.glossary import Glossary
from app.translation.patterns import PatternResult, PatternTranslator


class LLMProvider(Protocol):
    name: str
    is_generative: bool

    def translate_sentence(self, sentence: str, direction: str) -> PatternResult | None:
        """返回译文；无法可靠翻译时返回 None（调用方据此标记“不支持”，不得猜测）。"""

    def polish(self, text: str, allowed_numbers: set[str]) -> str:
        """润色已由模板生成的文本。输出中的数字必须是 allowed_numbers 的子集，否则调用方回退原文。"""

    def rewrite(self, text: str, *, instruction: str, allowed_numbers: set[str], must_keep=(), allow_digits: bool = True):
        """带完整校验的改写；返回 Rewrite(text, used, reason, redactions)，失败时 text 为原文。"""


class DeterministicProvider:
    name = "deterministic"
    is_generative = False

    def __init__(self, glossary: Glossary | None = None):
        self.glossary = glossary or Glossary.load()
        self.patterns = PatternTranslator.load(self.glossary)

    def translate_sentence(self, sentence: str, direction: str) -> PatternResult | None:
        return self.patterns.translate(sentence, direction)

    def polish(self, text: str, allowed_numbers: set[str]) -> str:
        return text  # 确定性实现不改写文本

    def rewrite(self, text: str, *, instruction: str, allowed_numbers: set[str], must_keep=(), allow_digits: bool = True):
        from app.llm.deepseek import Rewrite
        return Rewrite(text, False, "llm_disabled")


def get_provider(settings: Settings, glossary: Glossary | None = None) -> LLMProvider:
    if settings.llm_provider == "deterministic":
        return DeterministicProvider(glossary)
    if settings.llm_provider == "deepseek":
        from app.llm.deepseek import DeepSeekProvider
        return DeepSeekProvider(settings, glossary)
    raise NotImplementedError(
        f"LLM provider {settings.llm_provider!r} 尚未实现；当前支持 deterministic、deepseek。"
    )


def provider_of(state) -> LLMProvider:
    """按 app.state 缓存 provider（加载术语表 / 句型较慢，不必每次请求重建）。"""
    if getattr(state, "llm_provider_obj", None) is None:
        state.llm_provider_obj = get_provider(state.settings)
    return state.llm_provider_obj
