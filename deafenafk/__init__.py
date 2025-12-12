from .deafenafk import DeafenAFK

async def setup(bot):
    await bot.add_cog(DeafenAFK(bot))
