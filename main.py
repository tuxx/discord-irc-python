import irc.bot
import discord
import requests
import asyncio
import logging
import json

# Load configuration from config.json
with open("config.json", "r") as config_file:
    config = json.load(config_file)

IRC_SERVER = config["irc_server"]
IRC_PORT = config["irc_port"]
IRC_NICKNAME = config["irc_nickname"]
DISCORD_BOT_TOKEN = config["discord_bot_token"]
IRC_TO_DISCORD_WEBHOOKS = config["irc_to_discord_webhooks"]
DISCORD_TO_IRC_CHANNELS = config["discord_to_irc_channels"]

# Logging flags

ENABLE_DISCORD_LOGGING = config.get("enable_discord_logging", True)
ENABLE_IRC_LOGGING = config.get("enable_irc_logging", True)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("relay_bot.log"),
        logging.StreamHandler(),
    ],
)
# Suppress discord.py logs if Discord logging is disabled
if not ENABLE_DISCORD_LOGGING:
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)

def log_if_enabled(log_function, is_enabled, message, *args):
    if is_enabled:
        log_function(message, *args)

class IRCRelayBot(irc.bot.SingleServerIRCBot):
    def __init__(self, server, port, nickname, username, realname, channel_webhook_map):
        super().__init__([(server, port)], nickname, realname)
        self.username = username
        self.realname = realname
        self.channel_webhook_map = channel_webhook_map
        self.connection_channels = list(channel_webhook_map.keys())
        logging.info("IRC bot initialized for server: %s:%d with nickname: %s", server, port, nickname)

    def start(self):
        try:
            logging.info("Attempting to connect to IRC server: %s:%d", IRC_SERVER, IRC_PORT)
            super().start()
        except Exception as e:
            logging.error("Error during IRC connection: %s", e)

    def on_connect(self, connection, event):
        logging.info("Connected to IRC server: %s:%d", IRC_SERVER, IRC_PORT)
        connection.nick(self._nickname)
        connection.user(self.username, self.realname)

    def on_welcome(self, connection, event):
        logging.info("Received welcome message from IRC server: %s", event.arguments)
        for channel in self.connection_channels:
            logging.info("Joining IRC channel: %s", channel)
            try:
                connection.join(channel)
            except Exception as e:
                logging.error("Failed to join channel %s: %s", channel, e)

    def on_pubmsg(self, connection, event):
        irc_channel = event.target
        nickname = event.source.split('!')[0]
        message = event.arguments[0]
        logging.debug("Message received on IRC channel %s: <%s> %s", irc_channel, nickname, message)

        if irc_channel in self.channel_webhook_map:
            self.send_to_discord(irc_channel, nickname, message)

    def send_to_discord(self, irc_channel, nickname, message):
        webhook_url = self.channel_webhook_map.get(irc_channel)
        if not webhook_url:
            logging.warning("No webhook URL configured for IRC channel: %s", irc_channel)
            return

        payload = {
            "username": nickname,
            "content": message,
        }
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            logging.debug("Message relayed to Discord: <%s> %s", nickname, message)
        except requests.RequestException as e:
            logging.error("Failed to send message to Discord: %s", e)

    def on_disconnect(self, connection, event):
        logging.warning("Disconnected from IRC server. Reconnecting...")
        self.start()

    def on_error(self, connection, event):
        logging.error("IRC Error: %s", event)

    def on_privmsg(self, connection, event):
        logging.info("Private message received: <%s> %s", event.source, event.arguments)

    def on_notice(self, connection, event):
        logging.info("Notice received: <%s> %s", event.source, event.arguments)

    def on_any_event(self, connection, event):
        logging.debug("IRC Event: %s: %s", event.type, event.arguments)

class DiscordRelayBot(discord.Client):
    def __init__(self, irc_bot, discord_to_irc_map):
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        super().__init__(intents=intents)

        self.irc_bot = irc_bot
        self.discord_to_irc_map = discord_to_irc_map
        log_if_enabled(logging.info, ENABLE_DISCORD_LOGGING, "Initialized Discord bot")

    async def on_ready(self):
        log_if_enabled(logging.info, ENABLE_DISCORD_LOGGING, "Discord bot logged in as %s", self.user)

    async def on_message(self, message):
        if message.author.bot:
            return

        discord_channel_id = str(message.channel.id)
        irc_channel = self.discord_to_irc_map.get(discord_channel_id)

        if irc_channel:
            formatted_message = f"{message.author.name}: {message.content}"
            log_if_enabled(logging.debug, ENABLE_DISCORD_LOGGING, "Relaying message to IRC channel %s: %s", irc_channel, formatted_message)
            self.irc_bot.connection.privmsg(irc_channel, formatted_message)


# Initialize the IRC bot with the required parameters
irc_bot = IRCRelayBot(
    IRC_SERVER,
    IRC_PORT,
    IRC_NICKNAME,
    username=IRC_NICKNAME,
    realname="Discord Relay Bot",
    channel_webhook_map=IRC_TO_DISCORD_WEBHOOKS
)

# Run the IRC bot in a separate thread
async def run_irc_bot():
    try:
        log_if_enabled(logging.info, ENABLE_IRC_LOGGING, "Starting IRC bot")
        # Run the IRC bot's connection loop in a separate thread
        await asyncio.to_thread(irc_bot.start)
    except Exception as e:
        log_if_enabled(logging.error, ENABLE_IRC_LOGGING, "IRC bot encountered an error: %s", e)

# Initialize the Discord bot with the mapping of Discord channel IDs to IRC channels
discord_bot = DiscordRelayBot(irc_bot, DISCORD_TO_IRC_CHANNELS)

# Run both bots
async def main():
    # Create tasks for both bots
    irc_task = asyncio.create_task(run_irc_bot())
    discord_task = asyncio.create_task(discord_bot.start(DISCORD_BOT_TOKEN))
    
    try:
        # Wait for both tasks to complete (or fail)
        await asyncio.gather(irc_task, discord_task)
    except Exception as e:
        logging.error(f"Error in main loop: {e}")
        # Make sure to close both bots
        if not irc_task.done():
            irc_task.cancel()
        if not discord_task.done():
            discord_task.cancel()
        raise

# Entry point
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log_if_enabled(logging.critical, ENABLE_DISCORD_LOGGING, "Fatal error: %s", e)
