from __future__ import annotations
import re
POLITICS=[r"\bсанкц",r"\bправительств|\bпрезидент|\bпарламент|\bгосдум",r"\bвыборы|\bпереговор|\bмеждународн|\bдоговор",r"\bбюджет|\bдефицит|\bналог|\bрегулятор",r"\bнато\b|\bес\b|\bоон\b"]
TRADING=[r"\bфрс\b|\bfed\b|\bставк|\bпроцентн",r"\bцб\b|\bцентробанк|\bбан[кк]\s+россии|\bцбр",r"\bиндекс|\bмоex|\brtsi|\bакци|\bоблигац",r"\bнефть|\bбрент|\bwti|\bгаз",r"\bдивиденды|\bотчёт|\bm&a|\bipo",r"\bбиткоин|\bbitcoin|\bbtc|\beth|\bethereum|\bкрипт"]
INFOSEC=[r"\bфстэк|\bфсб\b|\b187-?фз|\bкии\b|\bасу\s*тп|\bics\b|\bscada\b",r"\bуязвимост|\bzero-?day|\bcve-\d{4}-\d+",r"\bатака|\bвзлом|\bинцидент",r"\bsoc\b|\bsiem\b|\bedr\b|\bnac\b",r"\bvipnet|\bkics|\bгост"]
PERSONAL=[r"\bличн(ые|ых)\s+финанс",r"\bсемейн(ый|ые)\s+бюджет",r"\bипотек|\bвклад|\bкредит|\bдебет|\bкэшбек|\bналоговый вычет",r"\bсбережен|\bнакоплен|\bпенси",r"\bethf|\betf|\bоблигац(ии|ия)\s+для\s+населени"]
STREAMS={'politics':POLITICS,'trading':TRADING,'infosec':INFOSEC,'personal':PERSONAL}
def matches(text: str, stream: str)->bool:
    s=text.lower()
    for rx in STREAMS.get(stream, []):
        if re.search(rx, s): return True
    return False
