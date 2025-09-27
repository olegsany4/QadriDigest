
import re
import hashlib

def normalize_whitespace(text: str) -> str:
    text = re.sub(r"[\t\r]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text

def short_fp(s: str, size: int = 20) -> str:
    h = hashlib.blake2b(s.encode("utf-8"), digest_size=16).hexdigest()
    return h[:size]
