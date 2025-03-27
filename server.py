from flask import Flask, request, jsonify
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from datetime import datetime
import logging
import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

# Initialize Flask app
app = Flask(__name__)

# Load environment variables
load_dotenv()

# =============================================
# Token Management System
# =============================================
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "registered_tokens.json")

def load_tokens():
    """Robust token loading with multiple fallback strategies"""
    tokens = set()
    try:
        if not Path(TOKEN_FILE).exists():
            return tokens
            
        with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            
            if not content:
                return tokens
                
            # Attempt 1: Standard JSON parsing
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    tokens.update(t for t in data if isinstance(t, str) and len(t) > 10)
                    return tokens
            except json.JSONDecodeError:
                pass
                
            # Attempt 2: Line-by-line recovery
            tokens.update(
                line.strip() for line in content.splitlines() 
                if len(line.strip()) > 10 and ':' in line
            )
            
    except Exception as e:
        logging.error(f"Token loading failed: {str(e)}", exc_info=True)
        
    return tokens

def save_tokens():
    """Atomic token saving with backup"""
    try:
        # Create backup
        if Path(TOKEN_FILE).exists():
            Path(TOKEN_FILE).replace(f"{TOKEN_FILE}.bak")
            
        # Write new file
        temp_file = f"{TOKEN_FILE}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(list(registered_tokens), f, indent=2)
            
        # Atomic replace
        Path(temp_file).replace(TOKEN_FILE)
    except Exception as e:
        logging.error(f"Token save failed: {str(e)}")

# Initialize token storage
registered_tokens = load_tokens()
logging.info(f"Initialized with {len(registered_tokens)} registered tokens")

# =============================================
# Logging Configuration
# =============================================
class CustomFormatter(logging.Formatter):
    def format(self, record):
        if record.pathname.endswith('server.py') and 'POST /webhook' in record.getMessage():
            return ""
        return super().format(record)

def setup_logging():
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(CustomFormatter(
        fmt='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(console_handler)

setup_logging()

# =============================================
# Firebase Configuration
# =============================================
SERVICE_ACCOUNT_INFO = {
    "type": os.getenv("FIREBASE_TYPE"),
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
    "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL"),
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL")
}

# =============================================
# API Endpoints
# =============================================
@app.route('/register', methods=['POST'])
def register_device():
    try:
        data = request.get_json()
        if not data or 'token' not in data:
            return jsonify({"status": "error", "message": "Missing token"}), 400
            
        token = str(data['token']).strip()
        if not token or ':' not in token:
            return jsonify({"status": "error", "message": "Invalid token format"}), 400
            
        if token not in registered_tokens:
            registered_tokens.add(token)
            save_tokens()
            logging.info(f"Registered new token (Total: {len(registered_tokens)})")
            
        return jsonify({
            "status": "success",
            "total_devices": len(registered_tokens),
            "is_new": token not in registered_tokens
        }), 200
        
    except Exception as e:
        logging.error(f"Registration error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": "Internal server error"}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        if request.form and 'payload_json' in request.form:
            payload = json.loads(request.form['payload_json'])
            if 'extra' in payload and 'items' in payload['extra']:
                items = payload['extra']['items']
                if items:
                    top_item = max(items, key=lambda x: x.get('priceEach', 0) * x.get('quantity', 1))
                    return send_loot_notification(
                        top_item.get('name', 'Unknown'),
                        top_item.get('quantity', 1),
                        top_item.get('priceEach', 0) * top_item.get('quantity', 1),
                        payload.get('extra', {}).get('source')
                    )
                    
        return jsonify({"status": "ignored"}), 200
        
    except Exception as e:
        logging.error(f"Webhook error: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

# =============================================
# Notification System
# =============================================
def send_loot_notification(item_name, quantity, value, source=None):
    timestamp = datetime.now().strftime("%H:%M:%S")
    title = "OSRS Drop!"
    body = f"{quantity}x {item_name} ({value:,} gp)"
    if source:
        body += f" from {source}"
    body += f" at {timestamp}"
    
    results = []
    for token in registered_tokens:
        success, attempts = send_fcm_notification_with_retry(token, title, body)
        results.append({
            "token": token[:10] + "...",
            "status": "success" if success else "failed",
            "attempts": attempts
        })
    
    success_count = sum(1 for r in results if r['status'] == 'success')
    logging.info(f"Notifications sent: {success_count} successful, {len(results)-success_count} failed")
    
    return jsonify({
        "status": "success",
        "item": item_name,
        "quantity": quantity,
        "value": value,
        "time": timestamp,
        "notifications": results
    })

def send_fcm_notification_with_retry(token, title, body, max_retries=3):
    attempt = 0
    while attempt <= max_retries:
        attempt += 1
        try:
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
                                "sound": "default"
                            }
                        }
                    }
                },
                timeout=10
            )
            response.raise_for_status()
            return True, attempt
            
        except Exception as e:
            if attempt <= max_retries:
                time.sleep(2 ** attempt)
            else:
                logging.error(f"FCM failed for {token[:10]}...: {str(e)}")
                return False, attempt

# =============================================
# Debug Endpoints
# =============================================
@app.route('/tokens', methods=['GET'])
def list_tokens():
    return jsonify({
        "total_tokens": len(registered_tokens),
        "tokens": list(registered_tokens)
    })

@app.route('/force_reload', methods=['POST'])
def force_reload():
    global registered_tokens
    registered_tokens = load_tokens()
    return jsonify({
        "status": "success",
        "loaded_tokens": len(registered_tokens)
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)