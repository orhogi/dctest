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
    def _cancel_task(self, guild_id: int, member_id: int):
        key = (guild_id, member_id)
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()

    def _clear_return(self, guild_id: int, member_id: int):
        self._return_to.pop((guild_id, member_id), None)

    async def _get_target_afk(self, guild: discord.Guild) -> discord.VoiceChannel | None:
        channel_id = await self.config.guild(guild).channel_id()
        target = guild.get_channel(channel_id) if channel_id else guild.afk_channel
        return target if isinstance(target, discord.VoiceChannel) else None

    async def _move_to_afk_if_still_self_deaf(self, member: discord.Member):
        if not await self.config.guild(member.guild).enabled():
            return
        if not member.voice or not member.voice.channel:
            return
        if not member.voice.self_deaf:
            return

        target = await self._get_target_afk(member.guild)
        if not target:
            return
        if member.voice.channel.id == target.id:
            return

        # store where to return them
        self._return_to[(member.guild.id, member.id)] = member.voice.channel.id

        try:
            await member.move_to(target, reason="Self-deafen -> AFK")
        except (discord.Forbidden, discord.HTTPException):
            self._clear_return(member.guild.id, member.id)

    async def _move_back_if_needed(self, member: discord.Member):
        if not member.voice or not member.voice.channel:
            return

        target = await self._get_target_afk(member.guild)
        if not target:
            self._clear_return(member.guild.id, member.id)
            return

        # only move back if they're currently in the AFK target VC
        if member.voice.channel.id != target.id:
            return

        key = (member.guild.id, member.id)
        return_id = self._return_to.get(key)
        if not return_id:
            return

        return_chan = member.guild.get_channel(return_id)
        if not isinstance(return_chan, discord.VoiceChannel):
            self._clear_return(*key)
            return

        if return_chan.id == target.id:
            self._clear_return(*key)
            return

        try:
            await member.move_to(return_chan, reason="Undeafen -> return to previous VC")
        except (discord.Forbidden, discord.HTTPException):
            pass
        finally:
            self._clear_return(*key)

    # ---------- listener ----------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot or not member.guild:
            return

        if not await self.config.guild(member.guild).enabled():
            return

        key = (member.guild.id, member.id)
        target = await self._get_target_afk(member.guild)

        # left VC -> cleanup
        if after.channel is None:
            self._cancel_task(*key)
            self._clear_return(*key)
            return

        # Channel changed (includes bot moving them)
        if before.channel and after.channel and before.channel.id != after.channel.id:
            # If they got moved INTO the AFK target AND we already saved a return channel,
            # that's expected -> keep the return mapping.
            if target and after.channel.id == target.id and key in self._return_to:
                pass
            else:
                # Otherwise, treat it as a user moving around -> cleanup.
                self._cancel_task(*key)
                self._clear_return(*key)

        prev_self_deaf = bool(before.self_deaf)
        now_self_deaf = bool(after.self_deaf)

        # If they JOIN a VC already self-deafened, also trigger
        if before.channel is None and after.channel is not None and now_self_deaf:
            prev_self_deaf = False  # force trigger below

        # self-deafen => schedule move to AFK
        if not prev_self_deaf and now_self_deaf:
            self._cancel_task(*key)
            delay = await self.config.guild(member.guild).delay()

            async def runner():
                try:
                    if delay:
                        await asyncio.sleep(delay)
                    await self._move_to_afk_if_still_self_deaf(member)
                finally:
                    self._tasks.pop(key, None)

            self._tasks[key] = asyncio.create_task(runner())
            return

        # undeafen => move back (if we previously moved them)
        if prev_self_deaf and not now_self_deaf:
            self._cancel_task(*key)
            await self._move_back_if_needed(member)
