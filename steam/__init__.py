from .steamfix import SteamFix

async def setup(bot):
    await bot.add_cog(SteamFix(bot))
