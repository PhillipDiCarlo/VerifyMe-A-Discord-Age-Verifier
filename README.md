# Discord Age Verification Bot

## Description
This Discord bot provides age verification functionality for Discord servers using Onfido's identity verification service. It allows server administrators to set up age verification requirements and automatically assigns roles to verified users.

## Features
- Age verification using Onfido's identity enhanced checks
- Customizable role assignment for verified users
- Tiered subscription system for Discord servers
- Cooldown period for verification attempts
- Support for multiple countries (based on Onfido's supported regions)
- Analytics tracking for command usage

## Prerequisites
- Python 3.7+
- Discord Bot Token
- Onfido API Token
- PostgreSQL database

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/discord-age-verification-bot.git
   cd discord-age-verification-bot
   ```

2. Install required packages:
   ```
   pip install -r requirements.txt
   ```

3. Set up your environment variables in a `.env` file:
   ```
   DISCORD_BOT_TOKEN=your_discord_bot_token
   ONFIDO_API_TOKEN=your_onfido_api_token
   SECRET_KEY=your_secret_key
   REDIRECT_URI=your_redirect_uri
   DATABASE_URL=your_postgresql_database_url
   ```

## Usage

1. Start the bot:
   ```
   python bot.py
   ```

2. In a Discord server, use the following commands:
   - `!verify`: Initiates the age verification process
   - `!reverify`: Allows a user to go through the verification process again
   - `!set_role @Role`: (Admin only) Sets the role to be assigned to verified users
   - `!set_subscription [tier]`: (Admin only) Sets the subscription tier for the server

## Configuration

- Modify the `tier_requirements` dictionary in the code to adjust the member count limits for each subscription tier.
- Update the `COOLDOWN_PERIOD` constant to change the cooldown duration between verification attempts.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE.md](LICENSE.md) file for details.

## Disclaimer

This bot integrates with Onfido's identity verification service. Make sure to comply with all relevant data protection and privacy laws when using this bot.

## License

This project is proprietary software owned by [Your Company Name]. All rights reserved. See the [LICENSE.md](LICENSE.md) file for details.