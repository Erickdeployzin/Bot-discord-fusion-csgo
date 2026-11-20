from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.config import get_settings
from core.database import get_db_session
from core.logger import logger
from modules.automations.service import AutomationExecutorService
from modules.csgo.service import CsgoService
from modules.sales.service import SalesService
from modules.vip.service import VipService


class FusionScheduler:
    """Agendador assíncrono para expiração de VIPs, reservas PIX, automações e status A2S."""

    def __init__(self, bot) -> None:
        self.bot = bot
        settings = get_settings()
        self.scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def start(self) -> None:
        self.scheduler.add_job(
            self._job_every_minute,
            trigger="interval",
            minutes=1,
            id="fusion_minute_tasks",
            replace_existing=True,
            max_instances=1,
        )

        self.scheduler.add_job(
            self._job_vip_lifecycle,
            trigger="interval",
            minutes=5,
            id="fusion_vip_lifecycle",
            replace_existing=True,
            max_instances=1,
        )

        self.scheduler.add_job(
            self._job_csgo_servers_refresh,
            trigger="interval",
            minutes=2,
            id="fusion_csgo_a2s_poll",
            replace_existing=True,
            max_instances=1,
        )

        self.scheduler.start()
        logger.info("FusionScheduler iniciado com sucesso.")

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("FusionScheduler encerrado.")

    async def _job_every_minute(self) -> None:
        if not self.bot.is_ready():
            return
        try:
            async with get_db_session() as session:
                expired_orders = await SalesService.release_expired_orders(session)
                sent_autos = await AutomationExecutorService.run_due_automations(self.bot, session)
                if expired_orders or sent_autos:
                    logger.info(
                        f"[Scheduler] Pedidos expirados liberados: {expired_orders} | Automações enviadas: {sent_autos}"
                    )
        except Exception as exc:
            logger.exception(f"[Scheduler] Erro na rotina de 1 minuto: {exc}")

    async def _job_vip_lifecycle(self) -> None:
        if not self.bot.is_ready():
            return
        try:
            async with get_db_session() as session:
                expired_vips, notified_vips = await VipService.process_expirations_and_notifications(
                    self.bot, session
                )
                if expired_vips or notified_vips:
                    logger.info(
                        f"[Scheduler] VIPs expirados: {expired_vips} | Notificações de vencimento enviadas: {notified_vips}"
                    )
        except Exception as exc:
            logger.exception(f"[Scheduler] Erro na rotina de ciclo VIP: {exc}")

    async def _job_csgo_servers_refresh(self) -> None:
        try:
            async with get_db_session() as session:
                await CsgoService.refresh_all_servers(session)
        except Exception as exc:
            logger.exception(f"[Scheduler] Erro na atualização A2S CS:GO: {exc}")
