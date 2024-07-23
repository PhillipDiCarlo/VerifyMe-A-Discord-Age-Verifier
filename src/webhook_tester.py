import os
import stripe
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up Stripe API key
stripe.api_key = os.getenv('STRIPE_RESTRICTED_SECRET_KEY')

def retrieve_verification_session(session_id):
    try:
        session = stripe.identity.VerificationSession.retrieve(
            session_id,
            expand=['verified_outputs.dob']
        )
        print("Verification Session Retrieved:")
        print(session)
        dob = session.verified_outputs.get('dob')
        print(f"Date of Birth: {dob}")
    except stripe.error.StripeError as e:
        print(f"Stripe API error: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    # Replace 'vs_1PfPDOJZiVMQTim64vozIuBd' with your actual session ID
    session_id = "vs_1PfPDOJZiVMQTim64vozIuBd"
    retrieve_verification_session(session_id)
