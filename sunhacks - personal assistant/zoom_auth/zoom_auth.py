import os
import requests
import base64
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()

# For Server-to-Server OAuth, you need:
# ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET

ACCOUNT_ID = os.getenv('ZOOM_ACCOUNT_ID')
CLIENT_ID = os.getenv('ZOOM_CLIENT_ID')
CLIENT_SECRET = os.getenv('ZOOM_CLIENT_SECRET')

TOKEN_URL = 'https://zoom.us/oauth/token'
API_BASE = 'https://api.zoom.us/v2'

print(f"ACCOUNT_ID: {ACCOUNT_ID}")
print(f"CLIENT_ID: {CLIENT_ID}")
print(f"CLIENT_SECRET: {CLIENT_SECRET}")

if not ACCOUNT_ID or not CLIENT_ID or not CLIENT_SECRET:
    print("Error: Missing environment variables. You need:")
    print("ZOOM_ACCOUNT_ID=your_account_id")
    print("ZOOM_CLIENT_ID=your_client_id") 
    print("ZOOM_CLIENT_SECRET=your_client_secret")
    exit(1)

class ZoomAPI:
    def __init__(self):
        self.access_token = os.getenv('ZOOM_SECRET_TOKEN')  # Unused, overwritten by get_access_token
        self.get_access_token()
    
    def get_access_token(self):
        """Get access token using Server-to-Server OAuth"""
        credentials = f"{CLIENT_ID}:{CLIENT_SECRET}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        headers = {
            'Authorization': f'Basic {encoded_credentials}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'grant_type': 'account_credentials',
            'account_id': ACCOUNT_ID
        }
        
        print("Requesting access token...")
        print(f"Headers: {headers}")
        print(f"Data: {data}")
        response = requests.post(TOKEN_URL, headers=headers, data=data)
        
        print(f"Response Status: {response.status_code}")
        print(f"Response Headers: {response.headers}")
        print(f"Response Text: {response.text}")
        
        if response.status_code == 200:
            token_data = response.json()
            self.access_token = token_data['access_token']
            print(f"✅ Access token obtained: {self.access_token[:20]}...")
            
            with open('zoom_token.txt', 'w') as f:
                f.write(f"Access Token: {self.access_token}\n")
                f.write(f"Token Type: {token_data.get('token_type', 'Bearer')}\n")
                f.write(f"Expires In: {token_data.get('expires_in', 'N/A')} seconds\n")
            
            return True
        else:
            print(f"❌ Failed to get access token: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    
    def get_headers(self):
        return {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    def get_user_info(self):
        if not self.access_token:
            print("❌ No access token available")
            return None
            
        response = requests.get(f"{API_BASE}/users/me", headers=self.get_headers())
        
        if response.status_code == 200:
            user_data = response.json()
            print("✅ User info retrieved successfully:")
            print(f"   Name: {user_data.get('first_name', '')} {user_data.get('last_name', '')}")
            print(f"   Email: {user_data.get('email', '')}")
            print(f"   Account ID: {user_data.get('account_id', '')}")
            return user_data
        else:
            print(f"❌ Failed to get user info: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    
    def get_meetings(self, user_id='me', meeting_type='scheduled'):
        if not self.access_token:
            print("❌ No access token available")
            return None
        
        params = {
            'type': meeting_type,
            'page_size': 30
        }
        
        response = requests.get(
            f"{API_BASE}/users/{user_id}/meetings", 
            headers=self.get_headers(),
            params=params
        )
        
        if response.status_code == 200:
            meetings_data = response.json()
            meetings = meetings_data.get('meetings', [])
            print(f"✅ Found {len(meetings)} {meeting_type} meetings:")
            
            for meeting in meetings[:5]:
                print(f"   📅 {meeting.get('topic', 'No topic')}")
                print(f"      ID: {meeting.get('id', 'N/A')}")
                print(f"      Start: {meeting.get('start_time', 'N/A')}")
                print(f"      Join URL: {meeting.get('join_url', 'N/A')}")
                print(f"      Password: {meeting.get('password', 'No password')}")
                print("   ---")
            
            return meetings_data
        else:
            print(f"❌ Failed to get meetings: {response.status_code}")
            print(f"Response: {response.text}")
            return None
    
    def get_meeting_details(self, meeting_id):
        if not self.access_token:
            print("❌ No access token available")
            return None
        
        response = requests.get(
            f"{API_BASE}/meetings/{meeting_id}", 
            headers=self.get_headers()
        )
        
        if response.status_code == 200:
            meeting_data = response.json()
            print(f"✅ Meeting details for ID {meeting_id}:")
            print(f"   Topic: {meeting_data.get('topic', 'N/A')}")
            print(f"   Start Time: {meeting_data.get('start_time', 'N/A')}")
            print(f"   Duration: {meeting_data.get('duration', 'N/A')} minutes")
            print(f"   Join URL: {meeting_data.get('join_url', 'N/A')}")
            print(f"   Meeting ID: {meeting_data.get('id', 'N/A')}")
            print(f"   Password: {meeting_data.get('password', 'No password')}")
            
            return meeting_data
        else:
            print(f"❌ Failed to get meeting details: {response.status_code}")
            print(f"Response: {response.text}")
            return None

def main():
    print("🚀 Starting Zoom API test...")
    
    zoom = ZoomAPI()
    
    if not zoom.access_token:
        print("❌ Failed to authenticate with Zoom")
        return
    
    print("\n" + "="*50)
    print("1. Testing user info...")
    user_info = zoom.get_user_info()
    
    print("\n" + "="*50)
    print("2. Testing scheduled meetings...")
    meetings = zoom.get_meetings(meeting_type='scheduled')
    
    print("\n" + "="*50)
    print("3. Testing upcoming meetings...")
    upcoming = zoom.get_meetings(meeting_type='upcoming')
    
    print("\n✅ All tests completed!")
    print("💾 Access token saved to 'zoom_token.txt'")

if __name__ == '__main__':
    main()