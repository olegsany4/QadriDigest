import argparse
import asyncio
import logging

from quadridigest.config import settings
from quadridigest.logging import setup_logging
from quadridigest.fetch.sources import read_sources
from quadridigest.fetch.normalize import clean_text
from quadridigest.dedupe.clusters import ClusterService
from quadridigest.llm.client import LLMClient
from quadridigest.format.render import TemplateRenderer
from quadridigest.publish.telethon_client import build_client, start_bot
from quadridigest.publish.publisher import Publisher
from quadridigest.storage.db import init_db
from quadridigest.storage.repositories import PostsRepo
from quadridigest.types import PostRecord
from quadridigest.route.router import route_topic_by_source


def parse_args():
    ap = argparse.ArgumentParser(description="QadriDigest runner")
    ap.add_argument("--once", action="store_true", help="single run (default behavior)")
    ap.add_argument("--dry-run", action="store_true", help="no telegram publish, log only")
    ap.add_argument("--window", type=int, default=20, help="how many last items to fetch per source")
    ap.add_argument(
        "--stay-alive",
        type=int,
        default=0,
        help="seconds to keep bot online after publish to handle inline buttons (0 = disconnect immediately)",
    )
    return ap.parse_args()


async def run_once(dry: bool, window: int = 20, stay_alive: int = 0):
    """
    One pipeline pass:
      fetch (user session) -> clean -> dedupe -> LLM -> render -> publish (bot)
    Optionally keep bot online for `stay_alive` seconds to handle 'Подробнее' callbacks.
    """
    # Init services
    cluster = ClusterService()
    llm = LLMClient(data_dir=settings.data_dir, prompt_version=settings.prompt_version)
    renderer = TemplateRenderer()
    repo = PostsRepo()

    client = None
    publisher = None
    channel_map = None

    if not dry:
        # Start bot client for publishing
        client = build_client()
        await start_bot(client)
        publisher = Publisher(client)

        # Direct mapping to @usernames from .env (recommended)
        channel_map = {
            "politics": settings.target_politics,
            "trading": settings.target_trading,
            "pf": settings.target_pf,
            "infosec": settings.target_infosec,
        }

    try:
        # Read posts via user session fetcher (implemented inside read_sources)
        for raw in read_sources(window=window):
            clean = clean_text(raw.text)

            # Dedupe (cheap hash)
            fp = cluster.fingerprint(clean.text)
            if cluster.is_duplicate(fp):
                logging.info("Duplicate skipped: %s", fp)
                continue
            # Keep minimal state to avoid re-announce within this run
            cluster.find_or_create(fp, "trading")

            # --- HARD ROUTING BY SOURCE (no LLM topic) ---
            source = str(getattr(raw, "source", "") or "")
            topic = route_topic_by_source(source)
            if topic not in ("politics", "trading", "pf", "infosec"):
                logging.warning("Unknown topic '%s' for source '%s', fallback to 'politics'", topic, source)
                topic = "politics"

            # LLM annotation (mock or real) for headline/summary/full
            ann = llm.summarize(clean)
            full = llm.full(clean, evidence=ann.evidence)

            # Render messages
            short_msg = renderer.render_short(
                topic=topic,
                headline=ann.headline,
                summary=ann.summary,
                evidence=ann.evidence,
            )
            full_msg = renderer.render_full(
                topic=topic,
                full_text=full.full_text,
                evidence=ann.evidence,
            )

            # Log the routing decision
            logging.info("Route: source=%s → topic=%s", source, topic)

            if dry:
                logging.info("[DRY] %s → %s", topic, short_msg.splitlines()[0] if short_msg else "")
                continue

            # Publish to mapped channel
            channel = channel_map[topic]
            logging.info("Publish → %s (%s)", topic, channel)
            msg_id = await publisher.post_short(
                channel=channel,
                topic=topic,
                headline=ann.headline,
                summary=ann.summary,
                full_text=full_msg,
                fingerprint=fp,  # use local fp for idempotency
            )

            # Persist record
            repo.upsert(PostRecord(
                topic=topic,
                fingerprint=fp,
                headline=ann.headline,
                summary=ann.summary,
                full_text=full_msg,
                channel=str(channel),
                message_id=msg_id,
                expanded=False,
                prompt_version=settings.prompt_version,
            ))
    finally:
        if client:
            # Keep bot online for handling inline callbacks if requested
            if stay_alive and stay_alive > 0:
                try:
                    logging.info("Stay alive for %s seconds to handle callbacks…", stay_alive)
                    await asyncio.sleep(stay_alive)
                except Exception:
                    pass
            await client.disconnect()


def main():
    args = parse_args()
    setup_logging(settings.log_level, settings.data_dir)
    init_db()
    asyncio.run(run_once(dry=args.dry_run, window=args.window, stay_alive=args.stay_alive))


if __name__ == "__main__":
    main()
