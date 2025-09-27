
from quadridigest.utils import short_fp

def simhash_fingerprint(text: str) -> str:
    # Упрощенный устойчивый отпечаток (blake2b), в реале — simhash/minhash+ngrams
    return short_fp(text, size=20)
