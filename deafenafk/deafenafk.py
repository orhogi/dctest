import asyncio
import discord
from redbot.core import commands, Config


class DeafenAFK(commands.Cog):
    """Self-deafen => move to AFK VC. Undeafen => move back to previous VC."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=736129044, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,  # if None, use guild.afk_channel
            delay=0
        )

        # (guild_id, member_id) -> voice_channel_id to return to
        self._return_to: dict[tuple[int, int], int] = {}

        # Pending move tasks
        self._tasks: dict[tuple[int, int], asyncio.Task] = {}

        # Anti-race token per user
        self._nonce: dict[tuple[int, int], int] = {}

        # Guard against reacting to our own moves
        self._moving: set[tuple[int, int]] = set()

    # ---------- settings ----------
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    @commands.group(name="deafenafkset", invoke_without_command=True)
    async def deafenafkset(self, ctx: commands.Context):
        gconf = self.config.guild(ctx.guild)
        enabled = await gconf.enabled()
        channel_id = await gconf.channel_id()
        delay = await gconf.delay()

        target = ctx.guild.get_channel(channel_id) if channel_id else ctx.guild.afk_channel
        target_name = target.name if isinstance(target, discord.VoiceChannel) else "NOT SET"

        await ctx.send(
            f"**DeafenAFK**\n"
            f"- Enabled: `{enabled}`\n"
            f"- Target AFK VC: `{target_name}`\n"
            f"- Delay: `{delay}s`"
        )

    @deafenafkset.command(name="enable")
    async def _enable(self, ctx: commands.Context, enabled: bool):
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"DeafenAFK enabled: `{enabled}`")

    @deafenafkset.command(name="channel")
    async def _channel(self, ctx: commands.Context, channel: discord.VoiceChannel | None):
        await self.config.guild(ctx.guild).channel_id.set(channel.id if channel else None)
        await ctx.send(f"Target set to: `{channel.name if channel else 'SERVER AFK CHANNEL'}`")

    @deafenafkset.command(name="delay")
    async def _delay(self, ctx: commands.Context, seconds: int):
        seconds = max(0, min(seconds, 3600))
        await self.config.guild(ctx.guild).delay.set(seconds)
        await ctx.send(f"Delay set to `{seconds}s`")

    # ---------- helpers ----------
    def _key(self, member: discord.Member) -> tuple[int, int]:
        return (member.guild.id, member.id)

    def _bump_nonce(self, key: tuple[int, int]) -> int:
        self._nonce[key] = self._nonce.get(key, 0) + 1
        return self._nonce[key]

    def _cancel_task(self, key: tuple[int, int]):
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

    def _clear_return(self, key: tuple[int, int]):
        self._return_to.pop(key, None)

    async def _get_target_afk(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        channel_id = await self.config.guild(guild).channel_id()
        target = guild.get_channel(channel_id) if channel_id else guild.afk_channel
        return target if isinstance(target, discord.VoiceChannel) else None

    async def _safe_move(self, member: discord.Member, channel: discord.VoiceChannel, reason: str) -> bool:
        key = self._key(member)
        self._moving.add(key)
        try:
            await member.move_to(channel, reason=reason)
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False
        finally:
            # tiny delay so the voice cache updates before we process next event
            await asyncio.sleep(0.2)
            self._moving.discard(key)

    async def _maybe_return(self, member: discord.Member):
        """If member is in AFK target, not self-deaf, and we have a return channel saved -> move them back."""
        key = self._key(member)

        if not member.voice or not member.voice.channel:
            return

        target = await self._get_target_afk(member.guild)
        if not target:
            self._clear_return(key)
            return

        if member.voice.channel.id != target.id:
            return

        # Only return if they're NOT self-deaf anymore
        if member.voice.self_deaf:
            return

        return_id = self._return_to.get(key)
        if not return_id:
            return

        return_chan = member.guild.get_channel(return_id)
        if not isinstance(return_chan, discord.VoiceChannel):
            self._clear_return(key)
            return

        ok = await self._safe_move(member, return_chan, "Undeafen -> return to previous VC")
        self._clear_return(key)  # clear either way to avoid loops

        return ok

    async def _move_to_afk_if_still_self_deaf(self, member: discord.Member, expected_nonce: int):
        key = self._key(member)

        # kill stale tasks
        if self._nonce.get(key, 0) != expected_nonce:
            return

        if not await self.config.guild(member.guild).enabled():
            return

        if not member.voice or not member.voice.channel:
            return

        # Only trigger on SELF deafen
        if not member.voice.self_deaf:
            return

        target = await self._get_target_afk(member.guild)
        if not target:
            return

        # already there
        if member.voice.channel.id == target.id:
            return

        # remember where to return
        self._return_to[key] = member.voice.channel.id

        ok = await self._safe_move(member, target, "Self-deafen -> AFK")
        if not ok:
            self._clear_return(key)
            return

        # If by the time they're in AFK they're not self-deaf anymore, bounce them back immediately
        await self._maybe_return(member)

    # ---------- listener ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or not member.guild:
            return

        if not await self.config.guild(member.guild).enabled():
            return

        key = self._key(member)

        # ignore events caused by our own moves
        if key in self._moving:
            return

        # left voice -> cleanup
        if after.channel is None:
            self._bump_nonce(key)
            self._cancel_task(key)
            self._clear_return(key)
            return

        # If they're sitting in AFK and not self-deaf, always try to return (covers weird ordering)
        await self._maybe_return(member)

        prev_self_deaf = bool(before.self_deaf)
        now_self_deaf = bool(after.self_deaf)

        # self-deafen => schedule move to AFK
        if not prev_self_deaf and now_self_deaf:
            self._cancel_task(key)
            nonce = self._bump_nonce(key)
            delay = await self.config.guild(member.guild).delay()

            async def runner():
                try:
                    if delay:
                        await asyncio.sleep(delay)
                    await self._move_to_afk_if_still_self_deaf(member, nonce)
                finally:
                    self._tasks.pop(key, None)

            self._tasks[key] = asyncio.create_task(runner())
            return

        # undeafen => cancel pending (and maybe_return already handles moving back)
        if prev_self_deaf and not now_self_deaf:
            self._bump_nonce(key)
            self._cancel_task(key)
            await self._maybe_return(member)
