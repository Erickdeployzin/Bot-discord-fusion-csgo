import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.embeds.factory import EmbedFactory
from core.logger import logger
from database.models.guild import GuildConfig, LogCategory
from database.models.vip import UserSteamLink, UserVip, VipPlan, VipStatus
from modules.logs.service import LogService


STEAMID64_BASE = 76561197960265728
STEAMID2_RE = re.compile(r"^STEAM_[0-5]:([01]):(\d+)$", re.IGNORECASE)
STEAMID3_RE = re.compile(r"^\[?U:1:(\d+)\]?$", re.IGNORECASE)
STEAMID64_RE = re.compile(r"^7656119\d{10}$")


def normalize_steam_ids(raw_input: str) -> tuple[str, str]:
    """
    Converte SteamID64, SteamID2 (STEAM_1:0:12345) ou SteamID3 ([U:1:24690])
    para o par canônico (steam_id64, steam_id2).
    """
    cleaned = raw_input.strip()

    if STEAMID64_RE.match(cleaned):
        sid64 = int(cleaned)
        account_id = sid64 - STEAMID64_BASE
        if account_id < 0:
            raise ValueError("SteamID64 inválido.")
        y = account_id % 2
        z = account_id // 2
        return str(sid64), f"STEAM_1:{y}:{z}"

    m2 = STEAMID2_RE.match(cleaned)
    if m2:
        y = int(m2.group(1))
        z = int(m2.group(2))
        sid64 = STEAMID64_BASE + (z * 2) + y
        return str(sid64), f"STEAM_1:{y}:{z}"

    m3 = STEAMID3_RE.match(cleaned)
    if m3:
        account_id = int(m3.group(1))
        sid64 = STEAMID64_BASE + account_id
        y = account_id % 2
        z = account_id // 2
        return str(sid64), f"STEAM_1:{y}:{z}"

    raise ValueError(
        "Formato de SteamID inválido. Informe um **SteamID64** (ex: `76561198000000000`) "
        "ou **SteamID2** (ex: `STEAM_1:0:123456`)."
    )


class VipService:
    """Gerencia ativação, renovação, vínculo Steam, notificações preventivas e revogação automática."""

    @staticmethod
    async def link_steam_account(
        session: AsyncSession,
        discord_user_id: int,
        raw_steam_id: str,
    ) -> tuple[bool, str, Optional[UserSteamLink]]:
        try:
            steam_id64, steam_id2 = normalize_steam_ids(raw_steam_id)
        except ValueError as exc:
            return False, f"❌ {exc}", None

        # Verifica se o SteamID já pertence a outro usuário Discord
        existing_steam = (
            await session.execute(
                select(UserSteamLink).where(UserSteamLink.steam_id64 == steam_id64)
            )
        ).scalar_one_or_none()
        if existing_steam and existing_steam.discord_user_id != discord_user_id:
            return False, "❌ Este SteamID já está vinculado a outra conta Discord.", None

        user_link = (
            await session.execute(
                select(UserSteamLink).where(UserSteamLink.discord_user_id == discord_user_id)
            )
        ).scalar_one_or_none()

        if user_link:
            user_link.steam_id64 = steam_id64
            user_link.steam_id2 = steam_id2
            user_link.verified = True
        else:
            user_link = UserSteamLink(
                discord_user_id=discord_user_id,
                steam_id64=steam_id64,
                steam_id2=steam_id2,
                verified=True,
            )
            session.add(user_link)

        await session.flush()
        return (
            True,
            f"✅ Conta Steam vinculada com sucesso!\n• **SteamID64:** `{steam_id64}`\n• **SteamID2:** `{steam_id2}`",
            user_link,
        )

    @staticmethod
    async def revoke_vip(
        bot: discord.Client,
        session: AsyncSession,
        guild: discord.Guild,
        user_vip_id: int,
        actor: Optional[discord.abc.User] = None,
        reason: str = "Expiração automática",
        is_expired: bool = False,
    ) -> tuple[bool, str]:
        stmt = (
            select(UserVip)
            .where(UserVip.id == user_vip_id, UserVip.guild_id == guild.id)
            .options(selectinload(UserVip.plan))
        )
        user_vip = (await session.execute(stmt)).scalar_one_or_none()
        if not user_vip:
            return False, "❌ Assinatura VIP não encontrada."

        if user_vip.status != VipStatus.ACTIVE:
            return False, "⚠️ Esta assinatura VIP já não está ativa."

        user_vip.status = VipStatus.EXPIRED if is_expired else VipStatus.REVOKED
        user_vip.revoked_reason = reason

        # Verifica se o usuário possui outra assinatura ativa usando o mesmo cargo antes de removê-lo
        other_same_role = (
            await session.execute(
                select(UserVip)
                .join(VipPlan, UserVip.plan_id == VipPlan.id)
                .where(
                    UserVip.guild_id == guild.id,
                    UserVip.user_id == user_vip.user_id,
                    UserVip.id != user_vip.id,
                    UserVip.status == VipStatus.ACTIVE,
                    VipPlan.role_id == user_vip.plan.role_id,
                )
            )
        ).scalar_one_or_none()

        if not other_same_role:
            member = guild.get_member(user_vip.user_id)
            role = guild.get_role(user_vip.plan.role_id)
            if member and role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=f"VIP encerrado: {reason}")
                except discord.Forbidden:
                    logger.warning(f"[Guild {guild.id}] Sem permissão para remover cargo VIP {role.id}")

        await LogService.dispatch(
            bot,
            session,
            guild.id,
            LogCategory.VIPS,
            title="VIP Expirado" if is_expired else "VIP Revogado",
            description=f"Plano **{user_vip.plan.name}** encerrado para <@{user_vip.user_id}>.",
            actor=actor,
            fields=[
                ("Motivo", reason, False),
                ("Cargo", f"<@&{user_vip.plan.role_id}>", True),
            ],
        )

        return True, f"✅ VIP **{user_vip.plan.name}** de <@{user_vip.user_id}> encerrado."

    @staticmethod
    async def process_expirations_and_notifications(
        bot: discord.Client,
        session: AsyncSession,
    ) -> tuple[int, int]:
        """
        Tarefa periódica:
        1. Envia notificações de vencimento próximo conforme `plan.notify_days_before`.
        2. Expira VIPs vencidos e remove automaticamente o cargo no Discord.
        """
        now = datetime.now(timezone.utc)
        stmt = (
            select(UserVip)
            .where(
                UserVip.status == VipStatus.ACTIVE,
                UserVip.expires_at.is_not(None),
            )
            .options(selectinload(UserVip.plan))
        )
        active_timed_vips = (await session.execute(stmt)).scalars().all()

        expired_count = 0
        notified_count = 0

        for uv in active_timed_vips:
            assert uv.expires_at is not None
            guild = bot.get_guild(uv.guild_id)
            if not guild:
                continue

            if uv.expires_at <= now:
                ok, _ = await VipService.revoke_vip(
                    bot,
                    session,
                    guild,
                    uv.id,
                    reason="Prazo de validade expirado",
                    is_expired=True,
                )
                if ok:
                    expired_count += 1
                    guild_cfg = await session.get(GuildConfig, guild.id)
                    member = guild.get_member(uv.user_id)
                    if member:
                        embed = EmbedFactory.warning(
                            guild_cfg,
                            title="👑 Seu VIP Expirou",
                            description=(
                                f"Olá {member.mention}, seu plano **{uv.plan.name}** em **{guild.name}** expirou.\n"
                                "Para renovar e continuar aproveitando todos os benefícios nos servidores FUSION, "
                                "acesse a loja oficial no servidor (`/loja`)."
                            ),
                        )
                        try:
                            await member.send(embed=embed)
                        except discord.Forbidden:
                            pass
                continue

            # Notificações preventivas configuradas no plano (ex: [7, 3, 1] dias antes)
            remaining = uv.expires_at - now
            days_left = max(1, (remaining.total_seconds() + 86399) // 86400)
            thresholds = sorted(set(uv.plan.notify_days_before or []), reverse=True)
            already_notified = set(uv.notified_thresholds or [])

            for threshold in thresholds:
                if days_left <= threshold and threshold not in already_notified:
                    already_notified.add(threshold)
                    uv.notified_thresholds = sorted(already_notified)
                    notified_count += 1

                    guild_cfg = await session.get(GuildConfig, guild.id)
                    member = guild.get_member(uv.user_id)
                    if member:
                        embed = EmbedFactory.build(
                            guild_cfg,
                            title="⏳ Aviso de Vencimento VIP",
                            description=(
                                f"Olá {member.mention}, seu plano **{uv.plan.name}** em **{guild.name}** "
                                f"vencerá <t:{int(uv.expires_at.timestamp())}:R> "
                                f"(`{uv.expires_at.strftime('%d/%m/%Y %H:%M UTC')}`).\n\n"
                                "Renove antecipadamente pelo `/loja` para somar dias sem perder seus benefícios!"
                            ),
                        )
                        try:
                            await member.send(embed=embed)
                        except discord.Forbidden:
                            pass
                    break

        return expired_count, notified_count