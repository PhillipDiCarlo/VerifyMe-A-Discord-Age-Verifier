Great! Now that your virtual environment is set up and activated, let's start the Flask server and then you can debug both your bot and webhook files.

### Running the Flask Server

1. **Activate the Virtual Environment:**

   **In PowerShell:**
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

   **In Command Prompt:**
   ```cmd
   .\venv\Scripts\activate.bat
   ```

2. **Run the Flask Server:**

   ```sh
   python src/stripe_webhook_service.py
   ```

This will start the Flask server which will handle the incoming Stripe webhook requests.

### Debugging in VSCode

### Setting Up Launch Configurations in VSCode

To streamline the process, you can set up VSCode launch configurations:

1. **Open the Command Palette** (`Ctrl+Shift+P`)
2. **Select** `Debug: Open launch.json`
3. **Add configurations** for both the Flask server and the Discord bot

Here’s an example `launch.json` configuration:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Flask: Run Server",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/src/stripe_webhook_service.py",
            "console": "integratedTerminal",
            "env": {
                "FLASK_APP": "src/stripe_webhook_service",
                "FLASK_ENV": "development"
            }
        },
        {
            "name": "Python: Bot",
            "type": "python",
            "request": "launch",
            "program": "${workspaceFolder}/src/bot.py",
            "console": "integratedTerminal"
        }
    ]
}
```

### Using the Launch Configurations

1. **Run the Flask server:**
   - Open the Debug panel (`Ctrl+Shift+D`).
   - Select `Flask: Run Server` from the dropdown.
   - Click the green play button or press `F5`.

2. **Run the Discord bot:**
   - Open another instance of the Debug panel (`Ctrl+Shift+D`).
   - Select `Python: Bot` from the dropdown.
   - Click the green play button or press `F5`.

This setup will allow you to run and debug both the Flask server and the Discord bot concurrently within VSCode.

### Verifying the Setup

1. **Flask Server Running:**
   Open your browser or a tool like Postman to send a test webhook to your Flask server URL (e.g., `http://localhost:5431/stripe_webhook`).

2. **Discord Bot Running:**
   Interact with your Discord bot in a server to ensure it's running and responding to commands.

### Summary

1. **Activate Virtual Environment:**
   ```powershell
   .\venv\Scripts\Activate.ps1  # PowerShell
   .\venv\Scripts\activate.bat  # Command Prompt
   ```

2. **Run Flask Server:**
   ```sh
   python src/stripe_webhook_service.py
   ```

3. **Set Up VSCode Launch Configurations:**
   Add configurations for both the Flask server and the Discord bot in `launch.json`.

4. **Debug in VSCode:**
   - Use the `Flask: Run Server` configuration to start the Flask server.
   - Use the `Python: Bot` configuration to start the Discord bot.

By following these steps, you'll be able to debug your bot and webhook files efficiently in VSCode.