"""从 app.state 取（并按需创建）共享依赖。原先分散在各路由的 get_xxx(request) 里，抽出后 API 与 Agent 共用同一份实现。"""
from app.kb.indexer import load_index
from app.llm.base import get_provider, provider_of
from app.regulatory.config import load_regulatory_config
from app.regulatory.service import RegulatoryAssistant
from app.rules.engine import load_rule_sets
from app.translation.glossary import Glossary
from app.translation.service import TranslationService


def rule_sets_of(state):
    if getattr(state, "rule_sets", None) is None:
        state.rule_sets = load_rule_sets()
    return state.rule_sets


def assistant_of(state) -> RegulatoryAssistant:
    """索引不存在时抛 FileNotFoundError（API 转 503，Agent 工具转 knowledge_unavailable）。"""
    if getattr(state, "assistant", None) is None:
        index = load_index(state.settings.index_dir)
        state.assistant = RegulatoryAssistant(index, load_regulatory_config(), provider=provider_of(state))
    return state.assistant


def translation_service_of(state) -> TranslationService:
    if getattr(state, "translation_service", None) is None:
        glossary = Glossary.load()
        state.translation_service = TranslationService(get_provider(state.settings, glossary), glossary)
    return state.translation_service
