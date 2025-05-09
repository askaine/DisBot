import discord
import pytz
from discord.ext import tasks, commands
import json
import os
import aiohttp
from bs4 import BeautifulSoup
import re
from datetime import datetime, timedelta, timezone
from keep_alive import keep_alive


CHANNEL_ID = '623835976053162004'
HYPIXEL_API_KEY = os.getenv("HYPIXEL_API_KEY")


intents = discord.Intents.default()
intents.members = True
intents.messages = True



bot = commands.Bot(command_prefix='!', intents=intents)

user_monitored_users = {}
last_login_cache = {}
eastern = pytz.timezone("US/Eastern")  # Plancke's timezone
utc = pytz.utc

if os.path.exists('monitored_users.json'):
    try:
        with open('monitored_users.json', 'r') as file:
            user_monitored_users = json.load(file)
            if not isinstance(user_monitored_users, dict):
                user_monitored_users = {}
    except json.JSONDecodeError:
        user_monitored_users = {}


def parse_plancke_time(time_str):
    time_str = " ".join(time_str.split()[:-1])  # Remove timezone text (EST/EDT)

    try:
        naive_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M")  # Parse without timezone
        eastern_time = eastern.localize(naive_time)  # Attach EST timezone
        utc_time = eastern_time.astimezone(utc)  # Convert to UTC
        return utc_time
    except ValueError as e:
        print(f"Error parsing time: {e}")
        return None

async def get_last_login_from_hypixel(username):
    uuid_url = f"https://api.mojang.com/users/profiles/minecraft/{username}"
    async with aiohttp.ClientSession() as session:
        async with session.get(uuid_url) as resp:
            if resp.status != 200:
                print(f"Could not find UUID for {username}")
                return None
            data = await resp.json()
            uuid = data.get("id")

        hypixel_url = f"https://api.hypixel.net/player?key={HYPIXEL_API_KEY}&uuid={uuid}"
        async with session.get(hypixel_url) as resp:
            if resp.status != 200:
                print(f"Could not fetch data from Hypixel for {username}")
                return None
            data = await resp.json()
            last_login_ts = data.get("player", {}).get("lastLogin")

            if last_login_ts:
                dt = datetime.fromtimestamp(last_login_ts / 1000,
                                            tz=timezone.utc)
                print(f"{username} last logged in at {dt}")
                return dt
            else:
                print(f"No login data for {username}")
                return None

@bot.command(name="adduser")
async def add_user(ctx, minecraft_username: str):
    """Adds a Minecraft username to the list of players monitored by the user."""
    discord_user_id = str(ctx.author.id)

    if discord_user_id not in user_monitored_users:
        user_monitored_users[discord_user_id] = []

    if minecraft_username in user_monitored_users[discord_user_id]:
        await ctx.send(f"⚠️ **{minecraft_username}** is already in your monitored list.")
    else:
        user_monitored_users[discord_user_id].append(minecraft_username)
        save_monitored_users()
        await ctx.send(f"✅ Added **{minecraft_username}** to your monitored list!")


@bot.command(name="removeuser")
async def remove_user(ctx, minecraft_username: str):
    """Removes a Minecraft username from the user's monitored list."""
    discord_user_id = str(ctx.author.id)

    if discord_user_id in user_monitored_users and minecraft_username in user_monitored_users[discord_user_id]:
        user_monitored_users[discord_user_id].remove(minecraft_username)
        if not user_monitored_users[discord_user_id]:  # Remove empty lists
            del user_monitored_users[discord_user_id]

        save_monitored_users()
        await ctx.send(f"🗑️ Removed **{minecraft_username}** from your monitored list.")
    else:
        await ctx.send(f"⚠️ **{minecraft_username}** is not in your monitored list.")


def save_monitored_users():
    """Saves the monitored users to a JSON file."""
    with open('monitored_users.json', 'w') as file:
        json.dump(user_monitored_users, file, indent=4)

@bot.command(name="getlogin")
async def get_login(ctx, username: str):
    """Command to get the last login of a specified player from Plancke.io."""
    last_login = await get_last_login_from_hypixel(username)

    if last_login:
        await ctx.send(f"**{username}** last logged in at **{last_login}**")
    else:
        await ctx.send(f"Could not find last login info for **{username}**.")



@tasks.loop(minutes=5)
async def update_login_cache():
    global last_login_cache
    last_login_cache = {}
    for user_list in user_monitored_users.values():
        for username in user_list:
            last_login = await get_last_login_from_hypixel(username)
            if last_login:
                last_login_cache[username] = last_login


@tasks.loop(seconds=60)  # Runs every 60 seconds
async def notify_online_players():
    now = datetime.utcnow().replace(tzinfo=timezone.utc)  # Make UTC-aware
    three_minutes_ago = now - timedelta(minutes=10)
    print(three_minutes_ago)
    print(f"Checking for players online in the last 10 minutes...")  # Debug print

    for username, last_login in last_login_cache.items():
        print(f"Checking {username}: last login at {last_login}")  # Debug print

        if last_login >= three_minutes_ago:
            for discord_user_id, monitored_players in user_monitored_users.items():
                if username in monitored_players:  # This user is tracking the player
                    print(f"{username} is being tracked by {discord_user_id}")  # Debug print

                    user = bot.get_user(int(discord_user_id))  # Try fetching from cache
                    if user is None:
                        user = await bot.fetch_user(int(discord_user_id))  # Fetch directly

                    if user:
                        mention = f"<@{discord_user_id}>"  # Format mention
                        await user.send(f"🔔 {mention} **{username}** just logged into Hypixel!")
                        print(f"✅ Notified {user.name} that {username} is online.")
                    else:
                        print(f"⚠️ Could not find Discord user {discord_user_id}")


@bot.event
async def on_ready():
    print(f'Bot {bot.user.name} connected!')
    update_login_cache.start()
    if not notify_online_players.is_running():
        notify_online_players.start()

@bot.command(name='recentonline')
async def recent_online(ctx):
    now = datetime.utcnow()
    recently_online_users = [username for username, last_login in last_login_cache.items()
                             if last_login >= now - timedelta(days=1)]
    await ctx.send(f"Players online in the last 24 hours:\n" + "\n".join(recently_online_users) if recently_online_users else "No recent logins.")

@bot.command(name='lastlogin')
async def last_login(ctx):
    last_login_times = [f"{username}: {last_login.strftime('%Y-%m-%d %H:%M:%S')} UTC" for username, last_login in last_login_cache.items()]
    await ctx.send("Last login times:\n" + "\n".join(last_login_times) if last_login_times else "No login data available.")

@bot.command(name='lastonline')
async def last_online(ctx):
    if last_login_cache:
        most_recent_user, most_recent_time = max(last_login_cache.items(), key=lambda x: x[1])
        await ctx.send(f"Most recently online: {most_recent_user} at {most_recent_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    else:
        await ctx.send("No login information available.")

keep_alive()
token = os.environ['DISCORD_BOT_TOKEN']
bot.run(token)

