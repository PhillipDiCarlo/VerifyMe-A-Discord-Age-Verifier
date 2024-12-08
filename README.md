# Discord Age Verification Bot

## Description
This Discord bot provides age verification functionality for Discord servers using Stripe's identity verification service. It allows server administrators to set up age verification requirements and automatically assigns roles to verified users.

## Features
- Age verification using Stripe Identity
- Customizable role assignment for verified users
- Tiered subscription system for Discord servers
- Cooldown period for verification attempts
- Support for multiple countries (based on Stripe's supported regions)
- Analytics tracking for command usage

## Prerequisites
- Python 3.7+
- Discord Bot Token
- Stripe API Keys
- PostgreSQL database
- RabbitMQ server

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
   TEST_STRIPE_SECRET_KEY=your_test_stripe_secret_key
   STRIPE_SECRET_KEY=your_stripe_secret_key
   STRIPE_RESTRICTED_SECRET_KEY=your_stripe_restricted_secret_key
   STRIPE_WEBHOOK_SECRET=your_stripe_webhook_secret
   TEST_STRIPE_WEBHOOK_SECRET=your_test_stripe_webhook_secret
   DATABASE_URL=your_database_url
   SECRET_KEY=your_flask_secret_key
   RABBITMQ_HOST=your_rabbitmq_host
   RABBITMQ_PORT=your_rabbitmq_port
   RABBITMQ_USERNAME=your_rabbitmq_username
   RABBITMQ_PASSWORD=your_rabbitmq_password
   RABBITMQ_VHOST=your_rabbitmq_vhost
   RABBITMQ_QUEUE_NAME=your_rabbitmq_queue_name
   ```

## Usage

1. Start the Flask server to handle webhooks:
   ```
   python stripe_webhook_service.py
   ```

2. Start the bot:
   ```
   python bot.py
   ```

3. In a Discord server, use the following commands:
   - `/verify`: Initiates the age verification process
   - `/set_role @Role`: (Admin only) Sets the role to be assigned to verified users
   - `/set_subscription [tier]`: (Admin only) Sets the subscription tier for the server
   - `/server_info`: (Admin only) Displays the current server configuration
   - `/subscription_status`: (Admin only) Shows detailed subscription status
   - `/verification_logs`: (Admin only) View recent verification actions
   - `/ping`: Check if the bot is responsive

## Configuration
- Set up a Stripe webhook in your Stripe dashboard to send 'identity.verification_session.verified' events to your `/stripe_webhook` endpoint.
- Modify the `tier_requirements` dictionary in the code to adjust the member count limits for each subscription tier.
- Update the `COOLDOWN_PERIOD` constant to change the cooldown duration between verification attempts.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is proprietary software owned by [Your Company Name]. All rights reserved. See the [LICENSE.md](LICENSE.md) file for details.

## Disclaimer

This bot integrates with Stripe's identity verification service. Make sure to comply with all relevant data protection and privacy laws when using this bot.