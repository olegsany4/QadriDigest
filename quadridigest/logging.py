
import logging, os, sys

def setup_logging(level: str = "INFO", data_dir: str = "./data"):
    os.makedirs(data_dir, exist_ok=True)
    log_path = os.path.join(data_dir, "qd.log")
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
