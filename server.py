from flask import Flask, request, jsonify
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from datetime import datetime
import logging
import json
import time
from logging.handlers import RotatingFileHandler
import os
from dotenv import load_dotenv

# Custom log formatter
class CustomFormatter(logging.Formatter):
    def format(self, record):
        if record.pathname.endswith('server.py'):
            if 'POST /webhook' in record.getMessage():
                # Skip Werkzeug's default request logs
                return ""
        return super().format(record)

# Configure logging
def setup_logging():
    # Disable Werkzeug's default handler
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # Create a custom logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Console handler with custom format
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(CustomFormatter(
        fmt='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    ))
    
    logger.addHandler(console_handler)

setup_logging()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
registered_tokens = set()

# Load environment variables
load_dotenv()

# Firebase credentials from .env
SERVICE_ACCOUNT_INFO = {
    "type": os.getenv("FIREBASE_TYPE"),
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),  # Fix newlines
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
    "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL"),
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL")
}

@app.route('/register', methods=['POST'])
def register_device():
    try:
        data = request.get_json()
        if not data or 'token' not in data:
            return jsonify({"status": "error", "message": "Missing token"}), 400
            
        token = data['token']
        registered_tokens.add(token)
        logging.info(f"Registered new token (Total: {len(registered_tokens)}): {token[:10]}...")
        
        return jsonify({
            "status": "success",
            "message": "Token registered",
            "registered_tokens": len(registered_tokens)
        }), 200
        
    except Exception as e:
        logging.error(f"Registration error: {str(e)}")
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if request.form:
            data = request.form.to_dict()
            if 'payload_json' in data:
                payload = json.loads(data['payload_json'])
                items = payload.get('extra', {}).get('items', [])
                
                if items:
                    # Get the most valuable item
                    top_item = max(items, key=lambda x: x.get('priceEach', 0) * x.get('quantity', 1))
                    
                    # Log cleanly
                    logging.info(
                        f"LOOT: {top_item['quantity']}x {top_item['name']} "
                        f"({top_item['priceEach'] * top_item['quantity']:,} gp) "
                        f"from {payload.get('extra', {}).get('source', 'Unknown')}"
                    )
                    
                    return handle_runelite_payload(payload)
        
        return jsonify({"status": "ignored"}), 200
    
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def handle_runelite_payload(data):
    """Process Runelite's specific payload format"""
    if 'extra' in data and 'items' in data['extra']:
        items = data['extra']['items']
        if items and len(items) > 0:
            # Get the most valuable item
            items_sorted = sorted(items, key=lambda x: x.get('priceEach', 0) * x.get('quantity', 1), reverse=True)
            item = items_sorted[0]
            return send_loot_notification(
                item.get('name', 'Unknown'),
                item.get('quantity', 1),
                item.get('priceEach', 0) * item.get('quantity', 1),
                data.get('extra', {}).get('source')
            )
    
    return jsonify({"status": "ignored", "message": "No valid items in Runelite payload"}), 200

def handle_simple_loot(data):
    """Process simple form-based loot notification"""
    return send_loot_notification(
        data.get('itemName', 'Unknown'),
        int(data.get('itemQuantity', 1)),
        int(data.get('itemValue', 0))
    )

def send_loot_notification(item_name, quantity, value, source=None):
    """Send notification to all registered devices with retry logic"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    title = "OSRS Drop!"
    body = f"{quantity}x {item_name} ({value:,} gp)"
    if source:
        body += f" from {source}"
    body += f" at {timestamp}"
    
    results = []
    successful_sends = 0
    failed_sends = 0
    
    for token in list(registered_tokens):
        try:
            success, attempts = send_fcm_notification_with_retry(token, title, body)
            if success:
                successful_sends += 1
                results.append({
                    "token": token[:10] + "...",
                    "status": "success",
                    "attempts": attempts
                })
            else:
                failed_sends += 1
                results.append({
                    "token": token[:10] + "...",
                    "status": "failed",
                    "attempts": attempts,
                    "error": "Max retries exceeded"
                })
        except Exception as e:
            failed_sends += 1
            results.append({
                "token": token[:10] + "...",
                "status": "error",
                "error": str(e)
            })
    
    # Log summary
    logging.info(
        f"Notification summary - Success: {successful_sends}, Failed: {failed_sends}, "
        f"Total devices: {len(registered_tokens)}"
    )
    
    return jsonify({
        "status": "success" if successful_sends > 0 else "partial" if failed_sends < len(registered_tokens) else "failed",
        "item": item_name,
        "quantity": quantity,
        "value": value,
        "time": timestamp,
        "successful_deliveries": successful_sends,
        "failed_deliveries": failed_sends,
        "notifications": results
    }), 200 if successful_sends > 0 else 207  # 207 Multi-Status

def send_fcm_notification_with_retry(token: str, title: str, body: str, max_retries: int = 4) -> tuple[bool, int]:
    """Send notification via FCM with retry logic"""
    attempt = 0
    last_error = None
    
    while attempt <= max_retries:
        try:
            attempt += 1
            credentials = service_account.Credentials.from_service_account_info(
                SERVICE_ACCOUNT_INFO,
                scopes=["https://www.googleapis.com/auth/firebase.messaging"]
            )
            credentials.refresh(Request())
            
            response = requests.post(
                f"https://fcm.googleapis.com/v1/projects/{SERVICE_ACCOUNT_INFO['project_id']}/messages:send",
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json"
                },
                json={
                    "message": {
                        "token": token,
                        "notification": {"title": title, "body": body},
                        "android": {
                            "priority": "high",
                            "notification": {
                                "channel_id": "osrs_notifications",
                                "sound": "default",
                                "visibility": "public"
                            }
                        }
                    }
                },
                timeout=10
            )
            
            response.raise_for_status()
            logging.info(f"Notification sent successfully to {token[:10]}... (attempt {attempt})")
            return True, attempt
            
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt <= max_retries:
                retry_delay = 2 ** attempt  # Exponential backoff (2, 4, 8 seconds)
                logging.warning(f"Attempt {attempt} failed for {token[:10]}... Retrying in {retry_delay}s: {last_error}")
                time.sleep(retry_delay)
            else:
                logging.error(f"Final attempt failed for {token[:10]}...: {last_error}")
                
    return False, attempt

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)