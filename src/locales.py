# locales.py — user-facing strings for VerifyMe, keyed by Discord locale code.
#
# Structure ported from VRCVerify. Lookup order (see bot.get_message):
#   server's configured instructions_locale > interaction locale > en-US.
# A key missing from a language falls back to en-US, so en-US must always
# contain every key.

# -- list of supported language codes --
LANGUAGE_CODES = [
    "en-US", "es-ES", "zh-CN", "ja", "de", "nl",
    "hi-IN", "ar", "bn", "pt-BR", "ru", "pa-IN",
]

# -- actual localized strings --
localizations: dict[str, dict[str, str]] = {
    "en-US": {
        # ----- verify flow -----
        "verify_dm_rejected":          "Please use this in the server you want to verify in, not in DMs.",
        "already_verified":            "You are already verified. Your role has been assigned.",
        "tier0_no_new_verifications":  "This tier does not support new user verification. Please contact the server owner or admin for assistance.",
        "age_below_minimum":           "You must be at least {minimum_age} years old to be added to the role.",
        "cooldown_active":             "You're in a cooldown period. Please wait {seconds} seconds before attempting to verify again.",
        "verification_limit_reached":  "This server has reached its monthly verification limit. Please contact an admin to upgrade the plan or wait until next month.",
        "verification_link":           "Click the link below to verify your age. This link is private and should not be shared:\n\n{url}",
        "verification_link_failed":    "Failed to initiate the verification process. Please try again later or contact support.",
        "unexpected_error":            "An unexpected error occurred. Please try again later or contact support.",
        "verification_canceled":       "Verification canceled for user {user_mention}",

        # ----- server-not-ready error embeds -----
        "embed_footer":                "Age Verification Service",
        "err_not_configured_title":    "Not configured",
        "err_not_configured_desc":     "This server is not set up. Ask an admin to run /setupverify.",
        "err_role_not_set_title":      "Role not set",
        "err_role_not_set_desc":       "Admins: configure the role with /setupverify before users can verify.",
        "err_sub_inactive_title":      "Subscription inactive",
        "err_sub_inactive_desc":       "This server doesn't have an active verification subscription.",

        # ----- DMs -----
        "dm_verified_title":           "You're verified",
        "dm_verified_desc":            "Your age was verified and your role was assigned.",
        "dm_role_success":             "You've been verified and given **{role}** in **{server}**!",
        "dm_role_failed_bot_position": "I couldn't assign the '{role}' role in {server}. This usually happens when the VerifyMe bot's role is not above the verified (and unverified) roles in the server's role list. Please ask a server admin to move the VerifyMe bot role above those roles and try again.",
        "dm_unverified_failed_bot_position": "Could not remove the {role} role in {server}. This usually happens when the VerifyMe bot's role is not above the unverified role. Ask a server admin to verify that the VerifyMe bot's role is above both the verified and unverified roles.",
        "dm_auto_verified":            "Welcome to **{server}**! You were already age-verified, so the **{role}** role was assigned automatically.",

        # ----- admin commands -----
        "no_permission":               "You do not have permission to use this command.",
        "setup_success":               "Verification role set to: {role} with minimum age {minimum_age}",
        "setup_unverified_set":        "\nUnverified role to remove on success: {role}",
        "not_configured_admin":        "This server is not configured for verification. Please type /setupverify to configure.",

        # ----- instructions panel -----
        "btn_verify_me":               "Verify Me",
        "instructions_title":          "Age Verification — What to expect",
        "instructions_desc":           "Already verified before? We'll automatically add the role if you meet this server's age requirement.\n\nNot verified yet? Click the 'Verify Me' button. You'll receive a private link to start the secure process.",
        "instructions_how_title":      "How it works",
        "instructions_how_value":      "1) Click 'Verify Me' to receive a private verification link.\n2) Open the link and follow the steps powered by Stripe Identity.\n3) You'll be asked to take photos of your ID (front and back) and a selfie.\n4) Stripe checks that your selfie matches your ID and confirms your age.\n5) Once complete, return to Discord—if you meet the minimum age, the role is assigned automatically.",
        "instructions_privacy_title":  "Privacy & Safety",
        "instructions_privacy_value":  "• Your link is private—do not share it.\n• Verification is handled by Stripe Identity.\n• Server staff only see your verification status (pass/fail) and apply roles accordingly.",
        "instructions_updated":        "Updated existing instructions message.",
        "instructions_posted":         "Posted instructions message and saved its location.",

        # ----- /settings paged view -----
        "settings_header":             "⚙️ VerifyMe Settings",
        "settings_page_min_age_title": "1.) Minimum age",
        "settings_page_min_age_desc":  "The minimum age required for the verified role in this server.",
        "settings_page_locale_title":  "2.) Instructions message language",
        "settings_page_locale_desc":   "Choose the language used for the instructions message, buttons, and bot replies in this server.",
        "settings_page_auto_verify_title": "3.) Auto verify new members on join",
        "settings_page_auto_verify_desc":  "If enabled, members already verified will automatically receive the role when they join (if they meet the minimum age).",
        "settings_page_custom_msg_title":  "4.) Custom success message",
        "settings_page_custom_msg_desc":   "Optional custom DM sent to users after successful verification (replaces the default).",
        "settings_page_unverified_title":  "5.) Unverified role",
        "settings_page_unverified_desc":   "Optional role that is removed from users once they verify.",
        "settings_current":            "Current: {value}",
        "settings_not_set":            "Not set",
        "settings_saved":              "Settings saved!",
        "settings_choose_yes_no":      "Choose Yes or No",
        "settings_choose_language":    "Choose a language",
        "settings_choose_role":        "Choose a role (optional)",
        "settings_btn_edit_message":   "Edit message",
        "settings_btn_clear_message":  "Clear message",
        "settings_btn_clear_role":     "Clear role",
        "settings_btn_change_age":     "Change age",
        "custom_msg_saved":            "Custom success message saved.",
        "custom_msg_cleared":          "Custom success message cleared. Default will be used.",
        "custom_msg_too_long":         "Message too long (max 1000 characters).",
        "custom_msg_invalid_links":    "Blocked: Only https links to discord.com or esattotech.com are allowed. Invalid link(s):\n{invalid_list}",
        "min_age_invalid":             "Please enter a whole number between 13 and 99.",
        "min_age_saved":               "Minimum age set to {minimum_age}.",

        # ----- misc -----
        "yes":                         "Yes",
        "no":                          "No",
    },
}
