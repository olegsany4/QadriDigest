from __future__ import annotations
import math, re
from dataclasses import dataclass
KEYWORDS={r"\bфрс\b|\bfed\b|\bфедеральн(ая|ого) резервн":1.2,r"\bцб\b|\bцбр|\bбан[кк]\s+россии|\bцентробанк":1.0,r"\bставк[аи]\b|\brate\b|\bсмягчен":1.0,r"\bинфляц":0.8,r"\bввп\b|\bрецес":0.8,r"\bакци[ия]\b|\bоблигац":0.8,r"\bиндекс\b|\bиндексы\b|\bммоex|\brtsi\b":0.8,r"\bбрент\b|\bwti\b|\bнефть\b|\bгаз\b":1.0,r"\bсанкц":0.7,r"\bэкспорт\b|\bимпорт\b|\bбаланс\b":0.6,r"\bбиткоин\b|\bbitcoin\b|\bbtc\b|\beth\b|\bethereum\b":0.9,r"\bбюджет\b|\bдефицит\b|\bфнб\b":0.7,r"\bфстэк|\bфсб\b|\bкии\b|\bасу\s*тп|\bics\b|\bscada\b|\bcve-":1.1}
TICKER_RE=re.compile(r"\b[A-Z]{2,5}\b"); NUMBER_RE=re.compile(r"\b\d{2,}\b"); PCT_RE=re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
@dataclass
class ScoreResult: score: float; reasons: list[str]
def score_text(text: str, views:int=0, forwards:int=0, has_link:bool=False)->ScoreResult:
    s=text.lower(); score=0.0; reasons=[]
    for rx,w in KEYWORDS.items():
        if re.search(rx,s): score+=w; reasons.append(f"+{w:.1f} kw")
    tickers=len(TICKER_RE.findall(text))
    if tickers: add=min(1.0,0.2*tickers); score+=add; reasons.append(f"+{add:.1f} tickers")
    if PCT_RE.search(s): score+=0.6; reasons.append("+0.6 %")
    nums=len(NUMBER_RE.findall(s))
    if nums>=3: score+=0.4; reasons.append("+0.4 nums")
    if views: score+=min(1.0, math.log10(max(1,views))/4); reasons.append("+engV")
    if forwards: score+=min(0.8, math.log10(max(1,forwards))/5); reasons.append("+engF")
    if has_link: score+=0.2; reasons.append("+0.2 link")
    if len(text)>1500: score-=0.2; reasons.append("-0.2 long")
    return ScoreResult(round(score,2), reasons)
