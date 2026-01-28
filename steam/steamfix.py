import asyncio
import re
import requests
from bs4 import BeautifulSoup
import discord
from redbot.core import commands

STEAM_WORKSHOP_REGEX = re.compile(
    r"https?://steamcommunity\.com/sharedfiles/filedetails/\?id=\d+"
)

class SteamFix(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        match = STEAM_WORKSHOP_REGEX.search(message.content)
        if not match:
            return

        # ⏳ wait for Discord to *try* embedding
        await asyncio.sleep(2)

        # refetch message
        message = await message.channel.fetch_message(message.id)

        # if Discord succeeded, do nothing
        if message.embeds:
            return

        url = match.group(0)

        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")

            img = soup.select_one("img.workshopItemPreviewImage")
            if not img or not img.get("src"):
                return

            embed = discord.Embed(
                title="Steam Workshop Preview",
                url=url,
                color=0x1b2838
            )
            embed.set_image(url=img["src"])

            await message.channel.send(embed=embed)

        except Exception:
            pass


async def setup(bot):
    await bot.add_cog(SteamFix(bot))
