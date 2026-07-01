import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from config import config
from src.domain_db.db import DomainDB
from src.domain_db.checker import DomainChecker
from src.domain_db.updater import DomainUpdater
from src.bypass.engine import BypassEngine
from src.rate_limiter import RateLimiter
from src.request_queue import RequestQueue
from src.utils import extract_urls

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = context.bot_data.get("domain_stats", {})
    await update.message.reply_text(
        f"🔗 <b>Short Link Bypass Bot</b>\n\n"
        f"Send me a shortened URL and I'll reveal the real destination.\n\n"
        f"<b>Known shorteners:</b> {stats.get('active_shorteners', '?')} active, "
        f"{stats.get('inactive_shorteners', '?')} dead\n"
        f"<b>Total in database:</b> {stats.get('total_domains', '?')}\n\n"
        f"Commands:\n"
        f"/bypass &lt;url&gt; - Bypass a shortened URL\n"
        f"/stats - Show database statistics\n"
        f"/help - Show this help",
        parse_mode="HTML",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine: BypassEngine = context.bot_data.get("engine")
    if not engine:
        await update.message.reply_text("Bot not fully initialized yet.")
        return
    stats = engine.checker.get_stats()
    await update.message.reply_text(
        f"📊 <b>Database Statistics</b>\n\n"
        f"Active shorteners: {stats['active_shorteners']}\n"
        f"Inactive/dead: {stats['inactive_shorteners']}\n"
        f"Total domains tracked: {stats['total_domains']}\n"
        f"Cached bypass results: {stats['cached_bypasses']}",
        parse_mode="HTML",
    )


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in config.admin_ids:
        await update.message.reply_text("Unauthorized.")
        return

    limiter: RateLimiter = context.bot_data.get("limiter")
    queue: RequestQueue = context.bot_data.get("queue")
    engine: BypassEngine = context.bot_data.get("engine")
    parts = []

    if limiter:
        ls = limiter.get_stats()
        parts.append(f"<b>Rate Limiter</b>\nActive users: {ls['active_users']}\nMax: {ls['max_requests']}/{ls['window_seconds']}s")

    if queue:
        qs = queue.get_stats()
        parts.append(f"<b>Request Queue</b>\nQueued: {qs['queued']}\nActive: {qs['active_slots']}/{qs['max_concurrent']}")

    if engine:
        db_stats = engine.checker.get_stats()
        parts.append(f"<b>Bypass Engine</b>\nCache: {db_stats['cached_bypasses']} entries\nDomains: {db_stats['total_domains']}")

    await update.message.reply_text("\n\n".join(parts), parse_mode="HTML")


async def bypass_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /bypass <url>")
        return
    url = context.args[0]
    await _process_bypass(update, context, url)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/"):
        return
    context.args = [text]
    await bypass_command(update, context)


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    known_domains: set[str] = context.bot_data.get("known_domains", set())
    if not known_domains:
        return

    urls = extract_urls(text)
    short_urls = []
    for url in urls:
        from src.utils import extract_domain
        domain = extract_domain(url)
        if domain and domain in known_domains:
            short_urls.append(url)

    if not short_urls:
        return

    url = short_urls[0]
    reply = await update.message.reply_text(f"🔍 Detected shortened link, bypassing...")
    context.args = [url]
    engine: BypassEngine = context.bot_data.get("engine")
    if not engine:
        await reply.edit_text("Bot not fully initialized yet.")
        return

    limiter: RateLimiter = context.bot_data.get("limiter")
    queue: RequestQueue = context.bot_data.get("queue")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    rl = limiter.consume(chat_id, user_id)
    if not rl.allowed:
        await reply.edit_text(f"⚠️ Rate limited. Try again in {rl.retry_after:.0f}s.")
        return

    if queue.is_queued(chat_id, user_id):
        await reply.edit_text("⏳ You already have a request in progress.")
        return

    pos = await queue.enqueue(chat_id, user_id)
    await reply.edit_text(f"⏳ You're #{pos} in queue...")

    try:
        await queue.acquire(chat_id, user_id)
        await reply.edit_text("🔍 Processing...")
        result = await engine.bypass(url)
    except Exception as e:
        logger.exception("Bypass failed")
        await reply.edit_text(f"❌ Error: {e}")
        return
    finally:
        queue.release()

    await reply.edit_text(result.user_message(), parse_mode="HTML", disable_web_page_preview=True)


async def _process_bypass(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    engine: BypassEngine = context.bot_data.get("engine")
    if not engine:
        await update.message.reply_text("Bot not fully initialized yet.")
        return

    limiter: RateLimiter = context.bot_data.get("limiter")
    queue: RequestQueue = context.bot_data.get("queue")
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    msg = await update.message.reply_text("⏳ Checking...")

    rl = limiter.consume(chat_id, user_id)
    if not rl.allowed:
        await msg.edit_text(f"⚠️ Rate limited. Try again in {rl.retry_after:.0f}s.")
        return

    if queue.is_queued(chat_id, user_id):
        await msg.edit_text("⏳ You already have a request in progress.")
        return

    pos = await queue.enqueue(chat_id, user_id)
    await msg.edit_text(f"⏳ You're #{pos} in queue...")

    try:
        await queue.acquire(chat_id, user_id)
        await msg.edit_text("🔍 Processing...")
        result = await engine.bypass(url)
    except Exception as e:
        logger.exception("Bypass failed")
        await msg.edit_text(f"❌ Error: {e}")
        return
    finally:
        queue.release()

    await msg.edit_text(result.user_message(), parse_mode="HTML", disable_web_page_preview=True)


async def post_init(application: Application):
    db = DomainDB(config.db_path)
    updater = DomainUpdater(db, refresh_days=config.domain_db_refresh_days)
    refresh_result = await updater.refresh()
    if refresh_result.get("refreshed"):
        logger.info(f"Domain DB refreshed: {refresh_result}")
    else:
        logger.info(f"Domain DB refresh skipped: {refresh_result.get('reason', 'n/a')}")

    checker = DomainChecker(db)
    engine = BypassEngine(db, checker)
    limiter = RateLimiter(
        max_requests=config.rate_limit_max,
        window_seconds=config.rate_limit_window,
    )
    queue = RequestQueue(max_concurrent=config.max_concurrent_bypasses)

    active_domains = set(db.get_all_domains_by_status("active"))

    application.bot_data["db"] = db
    application.bot_data["checker"] = checker
    application.bot_data["engine"] = engine
    application.bot_data["limiter"] = limiter
    application.bot_data["queue"] = queue
    application.bot_data["domain_stats"] = checker.get_stats()
    application.bot_data["known_domains"] = active_domains
    logger.info(f"Bot initialized — {len(active_domains)} known active shortener domains")
    logger.info(f"Rate limit: {config.rate_limit_max}/{config.rate_limit_window}s, "
                f"max concurrent: {config.max_concurrent_bypasses}")


async def shutdown(application: Application):
    engine: BypassEngine | None = application.bot_data.get("engine")
    if engine:
        await engine.close()
    checker: DomainChecker | None = application.bot_data.get("checker")
    if checker:
        await checker.close()


def main():
    if not config.bot_token:
        logger.error("BOT_TOKEN not set. Create a .env file or set the environment variable.")
        sys.exit(1)

    app = (
        Application.builder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_stop(shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("bypass", bypass_command))
    app.add_handler(CommandHandler("status", admin_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.ChatType.GROUPS | filters.ChatType.SUPERGROUP), handle_group_message))

    if config.use_webhook:
        if not config.webhook_url:
            logger.error("USE_WEBHOOK=true but WEBHOOK_URL is not set.")
            sys.exit(1)
        logger.info(f"Starting webhook on {config.webhook_listen}:{config.webhook_port} → {config.webhook_url}")
        app.run_webhook(
            listen=config.webhook_listen,
            port=config.webhook_port,
            url_path=config.bot_token,
            webhook_url=f"{config.webhook_url}/{config.bot_token}",
            secret_token=config.webhook_secret or None,
        )
    else:
        logger.info("Starting bot polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
