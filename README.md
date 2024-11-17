# discord-irc-python

![discord example](https://i.imgur.com/SCU0ktL.png)
![irc example](https://i.imgur.com/PUkiiqk.png)


# Description
Setup a bot to relay messages between discord and IRC.

## Config
Copy the config.json.sample to config.json and edit the parameters.

## Discord Setup
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to the "Bot" section and click "Add Bot"
4. Copy the bot token - you'll need this for the config.json
5. Go to OAuth2 > URL Generator
   - Select "bot" under Scopes
   - Select required permissions (minimum: Read Messages, Send Messages)
   - Use the generated URL to invite the bot to your server

### Setting up Webhooks
1. In your Discord server, go to Channel Settings > Integrations
2. Click "Create Webhook"
3. Give it a name and copy the webhook URL
4. Add the webhook URL to your config.json

## Running with docker
Make sure you made a `config.json`

```
docker-compose build
docker-compose up -d
```

## Setup development 

```
python3 -m venv virtual
source virtual/bin/activate
pip install -r requirements.txt
python main.py
```

## TODO
- [x] make sure mentions on irc to discord usernames work (@<discord_username>)
- [ ] Build docker image with github actions
