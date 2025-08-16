import irc.bot
import discord
import requests
import asyncio
import logging
import json
import hashlib
import re
import aiohttp
import time

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

IGNORED_IRC_NICKNAMES = config.get("ignored_irc_nicknames", [])
IGNORED_MESSAGE_PATTERNS = config.get("ignored_message_patterns", [])

class IRCRelayBot(irc.bot.SingleServerIRCBot):
    def __init__(self, server, port, nickname, username, realname, channel_webhook_map):
        super().__init__([(server, port)], nickname, realname)
        self.username = username
        self.realname = realname
        self.channel_webhook_map = channel_webhook_map
        self.connection_channels = list(channel_webhook_map.keys())
        self.discord_users = {}
        self.discord_emojis = {}
        logging.info("IRC bot initialized for server: %s:%d with nickname: %s", server, port, nickname)

        self.ignored_patterns = [re.compile(pattern) for pattern in IGNORED_MESSAGE_PATTERNS]
        self.ignored_nicknames = set(IGNORED_IRC_NICKNAMES)
        logging.info(f"Loaded {len(self.ignored_nicknames)} ignored nicknames and {len(self.ignored_patterns)} ignored patterns")

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

    def should_ignore_message(self, nickname, message):
        # Check if nickname is in ignored list
        if nickname.lower() in self.ignored_nicknames:
            logging.debug(f"Ignoring message from ignored nickname: {nickname}")
            return True
            
        # Check if message matches any ignored pattern
        for pattern in self.ignored_patterns:
            if pattern.search(message):
                logging.debug(f"Ignoring message matching pattern {pattern.pattern}: {message}")
                return True
                
        return False

    def on_pubmsg(self, connection, event):
        irc_channel = event.target
        nickname = event.source.split('!')[0]
        message = event.arguments[0]
        logging.debug("Message received on IRC channel %s: <%s> %s", irc_channel, nickname, message)

        # Check if message should be ignored
        if self.should_ignore_message(nickname, message):
            return

        # Handle !emoji command
        if message.strip().lower() == "!emoji":
            self.send_emoji_list(connection, nickname)
            return

        if irc_channel in self.channel_webhook_map:
            self.send_to_discord(irc_channel, nickname, message)

    def send_to_discord(self, irc_channel, nickname, message):
        webhook_url = self.channel_webhook_map.get(irc_channel)
        if not webhook_url:
            logging.warning("No webhook URL configured for IRC channel: %s", irc_channel)
            return

        # Translate @mentions in the message
        translated_message = self.translate_mentions(message, webhook_url)
        
        # Translate :emoji: to Discord emoji format
        if not self.discord_emojis:
            # Fetch emojis from all available guilds
            for guild in discord_bot.guilds:
                for emoji in guild.emojis:
                    self.discord_emojis[emoji.name.lower()] = str(emoji)
                logging.debug(f"Cached {len(guild.emojis)} emojis from guild {guild.name}")

        # Replace :emoji: with Discord emoji format
        words = translated_message.split()
        for i, word in enumerate(words):
            if word.startswith(':') and word.endswith(':'):
                emoji_name = word[1:-1].lower()  # Remove colons and convert to lowercase
                if emoji_name in self.discord_emojis:
                    words[i] = self.discord_emojis[emoji_name]
                    logging.debug(f"Translated emoji {emoji_name} to {self.discord_emojis[emoji_name]}")

        translated_message = ' '.join(words)

        payload = {
            "username": nickname,
            "content": translated_message,
        }
        try:
            response = requests.post(webhook_url, json=payload)
            response.raise_for_status()
            logging.debug("Message relayed to Discord: <%s> %s", nickname, translated_message)
        except requests.RequestException as e:
            logging.error("Failed to send message to Discord: %s", e)

    def translate_mentions(self, message, webhook_url):
        try:
            if not self.discord_users:
                # Get the guild ID from the Discord bot instead of webhook URL
                for guild in discord_bot.guilds:
                    bot_token = DISCORD_BOT_TOKEN
                    headers = {'Authorization': f'Bot {bot_token}'}
                    response = requests.get(f'https://discord.com/api/v10/guilds/{guild.id}/members?limit=1000', headers=headers)
                    
                    logging.debug(f"Discord API Response Status: {response.status_code}")
                    if response.status_code == 200:
                        members = response.json()
                        logging.debug(f"Fetched {len(members)} members from Discord")
                        
                        for member in members:
                            user = member['user']
                            user_id = user['id']
                            if 'global_name' in user and user['global_name']:
                                self.discord_users[user['global_name'].lower()] = user_id
                                logging.debug(f"Cached global_name: {user['global_name'].lower()} -> {user_id}")
                            username = user['username'].lower()
                            self.discord_users[username] = user_id
                            logging.debug(f"Cached username: {username} -> {user_id}")

            # Replace @mentions with Discord user IDs
            words = message.split()
            for i, word in enumerate(words):
                if word.startswith('@'):
                    username = word[1:].lower()  # Remove @ and convert to lowercase
                    logging.debug(f"Looking up mention: {username}")
                    if username in self.discord_users:
                        user_id = self.discord_users[username]
                        words[i] = f'<@{user_id}>'
                        logging.debug(f"Translated mention {username} to <@{user_id}>")
                    else:
                        logging.debug(f"No match found for {username}. Available users: {list(self.discord_users.keys())}")
            
            translated = ' '.join(words)
            logging.debug(f"Final translated message: {translated}")
            return translated
        except Exception as e:
            logging.error(f"Error translating mentions: {e}")
            return message

    def on_disconnect(self, connection, event):
        initial_delay = 5  # Start with 5 seconds
        max_delay = 300    # Maximum delay of 5 minutes (300 seconds)
        current_delay = initial_delay

        while True:
            logging.warning(f"Disconnected from IRC server. Attempting to reconnect in {current_delay} seconds...")
            try:
                time.sleep(current_delay)
                self.start()
                # If connection succeeds, break out of the loop
                break
            except Exception as e:
                logging.error(f"Reconnection attempt failed: {e}")
                # Double the delay for next attempt, but cap at max_delay
                current_delay = min(current_delay * 2, max_delay)

    def on_error(self, connection, event):
        logging.error("IRC Error: %s", event)

    def on_privmsg(self, connection, event):
        logging.info("Private message received: <%s> %s", event.source, event.arguments)

    def on_notice(self, connection, event):
        logging.info("Notice received: <%s> %s", event.source, event.arguments)

    def on_any_event(self, connection, event):
        logging.debug("IRC Event: %s: %s", event.type, event.arguments)

    def send_emoji_list(self, connection, nickname):
        try:
            # Refresh emoji cache if empty
            if not self.discord_emojis:
                for guild in discord_bot.guilds:
                    for emoji in guild.emojis:
                        self.discord_emojis[emoji.name.lower()] = str(emoji)

            # Sort emojis alphabetically
            sorted_emojis = sorted(self.discord_emojis.keys())
            
            # Split into chunks to avoid flooding
            chunk_size = 20
            emoji_chunks = [sorted_emojis[i:i + chunk_size] for i in range(0, len(sorted_emojis), chunk_size)]

            # Send header
            connection.privmsg(nickname, f"Available Discord emojis ({len(sorted_emojis)} total):")
            
            # Send emoji list in chunks
            for chunk in emoji_chunks:
                emoji_list = ", ".join(f":{emoji}:" for emoji in chunk)
                connection.privmsg(nickname, emoji_list)
                
            connection.privmsg(nickname, "Use these emojis by surrounding them with colons, e.g., :emoji_name:")
            
            logging.debug(f"Sent emoji list to {nickname}")
        except Exception as e:
            logging.error(f"Error sending emoji list: {e}")
            connection.privmsg(nickname, "Error retrieving emoji list. Please try again later.")

    def on_invite(self, connection, event):
        channel = event.arguments[0]
        inviter = event.source.split('!')[0]
        logging.info(f"Received invite to {channel} from {inviter}")
        try:
            connection.join(channel)
            logging.info(f"Successfully joined {channel} after invite")
        except Exception as e:
            logging.error(f"Failed to join {channel} after invite: {e}")

class DiscordRelayBot(discord.Client):
    def __init__(self, irc_bot, discord_to_irc_map):
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        super().__init__(intents=intents)

        self.irc_bot = irc_bot
        self.discord_to_irc_map = discord_to_irc_map
        self.username_colors = {}
        log_if_enabled(logging.info, ENABLE_DISCORD_LOGGING, "Initialized Discord bot")

    def get_user_color(self, username):
        if username not in self.username_colors:
            # Generate a consistent color number based on username
            hash_value = int(hashlib.md5(username.encode()).hexdigest(), 16)
            # IRC colors 2-13 (excluding 0,1,14,15 which are white/black/gray/white)
            color_number = (hash_value % 12) + 2
            self.username_colors[username] = color_number
        return self.username_colors[username]

    async def on_ready(self):
        log_if_enabled(logging.info, ENABLE_DISCORD_LOGGING, "Discord bot logged in as %s", self.user)

    async def upload_to_sourcebin(self, content, language='text'):
        try:
            # Clean the content by removing null bytes and normalizing line endings
            content = content.replace('\x00', '').replace('\r\n', '\n').replace('\r', '\n')
            
            payload = {
                "files": [{
                    "name": "code.txt",
                    "content": content,
                    "languageId": 294  # Always use plain text because idgaf (294)
                }]
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://sourceb.in/api/bins', 
                    json=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'User-Agent': 'Discord-IRC-Bridge/1.0'
                    }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return f"https://sourceb.in/{data['key']}"
                    else:
                        error_text = await response.text()
                        logging.error(f"Failed to upload to sourceb.in: {response.status}, Response: {error_text}")
                        return None
        except Exception as e:
            logging.error(f"Error uploading to sourceb.in: {e}")
            return None

    async def on_message(self, message):
        discord_channel_id = str(message.channel.id)
        irc_channel = self.discord_to_irc_map.get(discord_channel_id)

        if not irc_channel:
            return

        # Skip messages from our own bot
        if message.author.id == self.user.id:
            return

        # Skip messages from our own webhooks
        if message.webhook_id:
            webhook_url = f"https://discord.com/api/webhooks/{message.webhook_id}"
            if any(webhook_url in webhook for webhook in self.irc_bot.channel_webhook_map.values()):
                return

        # Get the effective name
        author_name = message.author.display_name
        content = message.content

        # Clean the content before processing
        content = message.content.replace('\x00', '').replace('\r\n', '\n').replace('\r', '\n')

        # Handle codeblocks
        if '```' in content:
            codeblock_pattern = r'```(?:(\w+)\n)?([\s\S]*?)```'
            for match in re.finditer(codeblock_pattern, content):
                language = match.group(1) or 'text'
                code = match.group(2).strip()
                
                # Upload to sourceb.in
                paste_url = await self.upload_to_sourcebin(code, language)
                if paste_url:
                    # Replace the codeblock with the URL
                    content = content.replace(match.group(0), f'[Code: {paste_url}]')
                else:
                    # If upload fails, truncate and clean the code
                    preview = code[:50].replace('\n', ' ') + "..." if len(code) > 50 else code.replace('\n', ' ')
                    content = content.replace(match.group(0), f'[Code: {preview}]')

        # Handle mentions
        for mention in message.mentions:
            content = content.replace(f'<@{mention.id}>', f'@{mention.display_name}')
            content = content.replace(f'<@!{mention.id}>', f'@{mention.display_name}')

        # Convert Discord emoji format <:name:id> to :name:
        content = re.sub(r'<(a)?:([a-zA-Z0-9_]+):[0-9]+>', r':\2:', content)

        # Collect all attachments and embeds
        attachment_urls = []
        if message.attachments:
            attachment_urls.extend([f"[{att.filename}: {att.url}]" for att in message.attachments])
        
        # Handle embeds (links, images, etc.)
        if message.embeds:
            embed_urls = [f"[{embed.type}: {embed.url}]" for embed in message.embeds if embed.url]
            attachment_urls.extend(embed_urls)

        # Format and send the main message
        color_code = self.get_user_color(author_name)
        
        # Send the main content first if it exists
        if content:
            formatted_message = f"<\x03{color_code}{author_name}\x03> {content}"
            formatted_message = formatted_message.replace('\n', ' ').strip()
            
            log_if_enabled(logging.debug, ENABLE_DISCORD_LOGGING, 
                           "Relaying message to IRC channel %s: %s", 
                           irc_channel, formatted_message)
            self.irc_bot.connection.privmsg(irc_channel, formatted_message)
        
        # Send attachments/embeds in separate messages to avoid IRC length limits
        # IRC typically has a 512-byte limit, so we'll be conservative and send each attachment separately
        if attachment_urls:
            for attachment_url in attachment_urls:
                attachment_message = f"<\x03{color_code}{author_name}\x03> {attachment_url}"
                
                log_if_enabled(logging.debug, ENABLE_DISCORD_LOGGING, 
                               "Relaying attachment to IRC channel %s: %s", 
                               irc_channel, attachment_message)
                self.irc_bot.connection.privmsg(irc_channel, attachment_message)


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
