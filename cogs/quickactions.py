from typing import Optional

import discord
from discord.ext import commands

import functions
import cooldowns
from cogs import starboard

qa_cooldown = cooldowns.CooldownMapping.from_cooldown(5, 5)


action_mapping = {
    "🗑️": "trash",
    "❄️": "freeze",
    "🔒": "lock"
}


async def is_orig(
    bot: commands.Bot,
    mid: int
) -> bool:
    conn = bot.db.conn
    async with bot.db.lock:
        async with conn.transaction():
            is_orig: bool = await conn.fetchval(
                """SELECT is_orig FROM messages
                WHERE id=$1""", mid
            )
    return is_orig


async def toggle_tashed(
    bot: commands.Bot,
    message_id: int,
    channel_id: int,
    guild_id: int
) -> None:
    get_message = \
        """SELECT * FROM messages
        WHERE id=$1"""
    update_message = \
        """UPDATE messages
        SET is_trashed=$1
        WHERE id=$2"""

    guild = bot.get_guild(guild_id)
    if guild is None:
        return

    conn = bot.db.conn

    async with bot.db.lock:
        async with bot.db.conn.transaction():
            mid, _cid = await functions.orig_message_id(
                bot.db, conn, message_id
            )
            sql_message = await conn.fetchrow(
                get_message, mid
            )
            trash: bool = not sql_message['is_trashed']
            await conn.execute(
                update_message, trash, mid
            )

    cid = _cid or channel_id
    channel = guild.get_channel(cid)
    if channel is None:
        return
    try:
        message = await functions.fetch(
            bot, mid, channel
        )
    except (discord.NotFound, discord.Forbidden):
        return

    await starboard.handle_starboards(
        bot.db, bot, mid, channel, message, guild
    )


class QuickActions(commands.Cog):
    """Allows trashing, forcing, and
    freezing with reactions"""

    def __init__(
        self,
        bot: commands.Bot
    ) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent
    ) -> None:
        action = action_mapping.get(payload.emoji.name)

        if action is None:
            return
        elif await is_orig(
            self.bot, payload.message_id
        ):
            return
        elif await functions.is_starboard_emoji(
            self.bot.db, payload.guild_id,
            payload.emoji
        ):
            return

        bucket: cooldowns.Cooldown = qa_cooldown.get_bucket(payload.member.id)
        retry_after = bucket.update_rate_limit()
        if retry_after is not None:
            return

        guild = self.bot.get_guild(payload.guild_id)
        channel = guild.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await functions.fetch(
                self.bot, payload.message_id, channel
            )
        except (discord.Forbidden, discord.NotFound):
            return

        await message.remove_reaction(
            payload.emoji.name, payload.member
        )

        if action == 'trash':
            await toggle_tashed(
                self.bot, payload.message_id,
                payload.channel_id, payload.guild_id
            )

    @commands.command(
        name='quickActions', aliases=['qa'],
        brief="Enable/disable quickactions"
    )
    @commands.has_guild_permissions(manage_guild=True)
    @commands.guild_only()
    async def quick_actions(
        self,
        ctx: commands.Context,
        enabled: Optional[bool] = None
    ) -> None:
        get_guild = \
            """SELECT * FROM guilds WHERE id=$1"""
        update_guild = \
            """UPDATE guilds
            SET is_qa_on=$1
            WHERE id=$2"""

        conn = self.bot.db.conn

        if enabled is None:
            async with self.bot.db.lock:
                async with conn.transaction():
                    sql_guild = await conn.fetchrow(
                        get_guild, ctx.guild.id
                    )
            is_enabled = sql_guild['is_qa_on']
            await ctx.send(
                "QuickActions are disabled for this server."
                if not is_enabled else
                "QuickActions are enabled for this server."
            )
        else:
            async with self.bot.db.lock:
                async with conn.transaction():
                    await conn.execute(
                        update_guild, enabled,
                        ctx.guild.id
                    )
            await ctx.send(
                "QuickActions have been enabled."
                if enabled else
                "QuickActions have been disabled."
            )


def setup(
    bot: commands.Bot
) -> None:
    bot.add_cog(QuickActions(bot))
