import asyncio
import re
import requests
from bs4 import BeautifulSoup
import discord
from redbot.core import commands

STEAM_REGEX = re.compile(
    r"https?://steamcommunity\.com/sharedfiles/filedetails/\?\S+",
    re.IGNORECASE
)

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cookie": "birthtime=568022401; lastagecheckage=1-0-1990;"
}

class SteamFix(commands.Cog):
    """Posts the first Steam Workshop image when Discord fails to embed"""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.content:
            return

        match = STEAM_REGEX.search(message.content)
        if not match:
            return

        # wait for Discord embed attempt
        await asyncio.sleep(2)
        message = await message.channel.fetch_message(message.id)

        # if Discord embedded successfully, do nothing
        if message.embeds:
            return

        url = match.group(0)

        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")

            img = (
                soup.select_one("img#previewImageMain") or
                soup.select_one("img.workshopItemPreviewImage") or
                soup.select_one("img[src*='steamusercontent']")
            )

            if not img or not img.get("src"):
                return

            embed = discord.Embed(
                title="Steam Workshop Preview",
                url=url,
                color=0x1b2838
            )
            embed.set_image(url=img["src"])

            await message.channel.send(embed=embed)

        except Exception as e:
            print("SteamFix error:", e)


async def setup(bot):
    await bot.add_cog(SteamFix(bot))
