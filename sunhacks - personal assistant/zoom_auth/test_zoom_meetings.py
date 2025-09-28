import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = 'https://api.zoom.us/v2'

def list_user_meetings(access_token, type_filter='scheduled', page_size=30):
    url = f"{API_BASE}/users/me/meetings"
    headers = {'Authorization': f'Bearer {access_token}'}
    params = {
        'type': 2 if type_filter == 'scheduled' else 0,
        'page_size': page_size
    }
    
    response = requests.get(url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        meetings = data.get('meetings', [])
        next_token = data.get('next_page_token')
        while next_token:
            params['next_page_token'] = next_token
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                meetings.extend(data.get('meetings', []))
                next_token = data.get('next_page_token')
            else:
                raise Exception(f"Pagination failed: {response.text}")
        return meetings
    else:
        raise Exception(f"API call failed: {response.status_code} - {response.text}")

if __name__ == '__main__':
    try:
        with open('zoom_token.txt', 'r') as f:
            lines = f.readlines()
            access_token = next(line.split(': ')[1].strip() for line in lines if line.startswith('Access Token'))
    except FileNotFoundError:
        print("Error: zoom_tokens.txt not found. Run zoom_auth.py first.")
        exit(1)
    
    try:
        meetings = list_user_meetings(access_token)
        if not meetings:
            print("No scheduled meetings found.")
        for meeting in meetings:
            print(f"Topic: {meeting['topic']}")
            print(f"Meeting ID: {meeting['id']}")
            print(f"Password: {meeting.get('password', 'None required')}")
            print(f"Start Time: {meeting['start_time']}")
            print(f"Join URL: {meeting['join_url']}")
            print(f"Duration (min): {meeting['duration']}")
            print("---")
    except Exception as e:
        print(f"Error fetching meetings: {e}")