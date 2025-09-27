from __future__ import annotations
from typing import Dict

# All KEYS MUST BE LOWERCASE! We'll normalize incoming source with lstrip("@").lower().
SOURCE_TO_TOPIC: Dict[str, str] = {
    # --- СМИ / Политика / Новости ---
    "readovkanews": "politics",
    "vedomosti": "politics",
    "ostorozhno_novosti": "politics",
    "headlines_geo": "politics",
    "interfaxonline": "politics",
    "bloomberg_ru": "politics",
    "kommersant": "politics",

    # --- Трейдинг / Рынки / Сигналы ---
    "bitkogan": "trading",
    "tb_invest_official": "trading",
    "markettwits": "trading",
    "cbrstocks": "trading",
    "banksta": "trading",
    "bankrollo": "trading",
    "newssmartlab": "trading",
    "thewallstreetpro": "trading",
    "selfinvestor": "trading",
    "if_market_news": "trading",
    "dengi_bitkogan": "trading",
    "nefte_baza": "trading",
    "smartlabnews": "trading",
    "headlines_for_traders": "trading",
    "headlines_quants": "trading",
    "headlines_macro": "trading",
    "ak47pfl": "trading",
    "sberinvestments": "trading",
    "bitkogan_hotline": "trading",
    "bcs_express": "trading",
    "russianmacro": "trading",
    "profitgate": "trading",
    "kira_pronira": "trading",
    "investfuture": "trading",
    "investor_catalog": "trading",  # verify exists
    "investheroes": "trading",
    "wallstreetqueenofficial": "trading",
    "if_stocks": "trading",
    "if_bonds": "trading",
    "gazpromneft_official": "trading",
    "finamalert": "trading",
    "investfundsru": "trading",
    "radium_finance": "trading",
    "alfabank": "trading",
    "rusetfs": "trading",

    # --- Личные финансы / Инвестблоги ---
    "hranidengi": "pf",
    "blogbankir": "pf",
    "cach_in_tablic": "pf",   # check name
    "finkod_vtb": "pf",
    "sberbank": "pf",
    "tbank": "pf",

    # --- Информационная безопасность ---
    "seclabnews": "infosec",
    "tg_security": "infosec",
    "haccing": "infosec",     # likely missing
    "codeby_sec": "infosec",
    "hack_less": "infosec",
    "irozysk": "infosec",
    "it_secur": "infosec",
}

def route_topic_by_source(source: str) -> str:
    """
    Map source (username or numeric id) to topic.
    Normalizes case and strips leading '@'.
    If not found, applies a tiny heuristic for 'infosec'-ish names.
    Fallback: 'politics'.
    """
    if not source:
        return "politics"
    key = source.lstrip("@").lower()

    # direct map
    topic = SOURCE_TO_TOPIC.get(key)
    if topic:
        return topic

    # tiny heuristic: if channel name contains 'sec', 'hack', 'infosec' → infosec
    for needle in ("infosec", "sec", "hack"):
        if needle in key:
            return "infosec"

    return "politics"
