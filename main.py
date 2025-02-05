import os
import pickle
import time
import speech_recognition as sr
from text_to_speech import speak_text
from dotenv import load_dotenv
import threading
from datetime import datetime
import queue
import json


# OpenAI imports
from openai import OpenAI, APIError

# Google Calendar and Selenium imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timedelta, timezone

# Load environment variables
load_dotenv()

# Google Calendar Scopes
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Define the termination keyword
TERMINATION_KEYWORD = "terminate"

# Initialize recognizer with adjusted parameters
recognizer = sr.Recognizer()
recognizer.dynamic_energy_threshold = True  # Use fixed energy threshold
recognizer.energy_threshold = 2000  # Adjust this value based on your environment
recognizer.pause_threshold = 0.8  # Reduce pause threshold for faster response

# Initialize the OpenAI client with timeout
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'), timeout=30.0)  # 30 second timeout

# Initialize conversation history
conversation_history = []

# Global variable to track meeting status
meeting_active = False

# Add with other global variables
notes_taker = None  # Initialize the global variable

def get_google_calendar_creds():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
    
    return creds

def setup_driver():
    chrome_options = Options()
    chrome_options.add_argument('--use-fake-ui-for-media-stream')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--start-maximized')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    
    # Add these new options
    chrome_options.add_argument('--disable-notifications')
    chrome_options.add_argument('--enable-automation')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # Create the driver
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        return driver
    except Exception as e:
        print(f"Error setting up WebDriver: {e}")
        return None

def join_meeting(driver, meet_url):
    global meeting_active
    if driver is None:
        print("WebDriver setup failed. Cannot join meeting.")
        return False

    try:
        print(f"Opening Google Meet URL: {meet_url}")
        driver.get(meet_url)
        time.sleep(8)  # Wait for initial load

        # Turn off camera using the known working method
        try:
            buttons = driver.find_elements(By.CSS_SELECTOR, 'div[role="button"]')
            for button in buttons:
                tooltip = button.get_attribute('data-tooltip')
                if tooltip and 'camera' in tooltip.lower():
                    driver.execute_script("arguments[0].click();", button)
                    print("Camera turned off")
                    break
        except Exception as e:
            print(f"Could not turn off camera: {str(e)}")

        # Set name
        try:
            time.sleep(2)
            name_input = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[aria-label='Your name']"))
            )
            name_input.clear()
            name_input.send_keys("AI Agent")
            print("Name set successfully")
        except Exception as e:
            print(f"Could not set name: {str(e)}")

        # Click Join button with multiple attempts
        try:
            time.sleep(3)  # Wait longer before trying to join
            join_buttons = driver.find_elements(By.CSS_SELECTOR, 
                'button[jsname*="join"], button[jscontroller*="join"]')
            
            for button in join_buttons:
                try:
                    if any(text in button.text.lower() for text in ['join now', 'ask to join']):
                        button.click()
                        time.sleep(5)
                        print(f"Clicked join button with text: {button.text}")
                        break
                except:
                    continue

            # If the above didn't work, try XPath
            if not join_buttons:
                join_xpath_buttons = driver.find_elements(By.XPATH, 
                    "//*[contains(text(), 'Join now') or contains(text(), 'Ask to join')]")
                for button in join_xpath_buttons:
                    try:
                        button.click()
                        time.sleep(5)
                        print(f"Clicked join button using XPath: {button.text}")
                        break
                    except:
                        continue

            # Enhanced verification of meeting join
            max_attempts = 5
            for attempt in range(max_attempts):
                try:
                    # More comprehensive check for meeting presence
                    meeting_indicators = [
                        'div[jscontroller*="meeting"]',
                        'div[data-meeting-code]',
                        'div[role="presentation"]',
                        'div[aria-label*="meeting"]'
                    ]
                    
                    in_meeting = False
                    for selector in meeting_indicators:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        if elements:
                            in_meeting = True
                            break
                    
                    if in_meeting:
                        print("Successfully joined meeting")
                        meeting_active = True
                        return True
                    
                    print(f"Attempt {attempt + 1}: Not in meeting yet. Retrying...")
                    time.sleep(3)
                
                except Exception as check_e:
                    print(f"Meeting verification error: {check_e}")
            
            print("Failed to confirm meeting join after multiple attempts")
            meeting_active = False
            return False

        except Exception as e:
            print(f"Could not handle join button: {str(e)}")
            meeting_active = False
            return False

    except Exception as e:
        print(f"An error occurred during meeting join: {str(e)}")
        meeting_active = False
        return False

def find_and_join_meeting():
    global meeting_active
    print("Finding upcoming meetings")
    creds = get_google_calendar_creds()
    service = build('calendar', 'v3', credentials=creds)

    # Calculate time range
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=1)).isoformat()  # Look for events in the next 24 hours

    events_result = service.events().list(
        calendarId='primary',
        timeMin=time_min,
        timeMax=time_max,
        maxResults=10,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    if not events:
        print('No upcoming events found.')
        # If no events found, try joining a specific meeting URL
        meet_url = "https://meet.google.com/zbu-odsb-dfc"  # Replace with your test meeting URL
        print(f"No calendar events found. Trying to join specific meeting: {meet_url}")
        driver = setup_driver()
        join_meeting(driver, meet_url)
        return driver

    # Look for meetings with Google Meet links
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        print(f"Meeting: {event['summary']}")
        print(f"Start Time: {start}")
        
        # Check for Meet link in event
        meet_link = None
        
        # Check conferenceData first
        if 'conferenceData' in event and 'entryPoints' in event['conferenceData']:
            for entryPoint in event['conferenceData']['entryPoints']:
                if entryPoint['entryPointType'] == 'video':
                    meet_link = entryPoint['uri']
        
        # If no conferenceData, check hangoutLink
        if not meet_link and 'hangoutLink' in event:
            meet_link = event['hangoutLink']
            
        if meet_link:
            print(f"Google Meet Link: {meet_link}")
            driver = setup_driver()
            join_meeting(driver, meet_link)
            return driver

    # If no events with Meet links found, try joining a specific meeting
    meet_url = "https://meet.google.com/zbu-odsb-dfc"  # Replace with your test meeting URL
    print(f"No meetings with Meet links found. Trying to join specific meeting: {meet_url}")
    driver = setup_driver()
    join_meeting(driver, meet_url)
    return driver

def generate_response(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are an AI voice agent, a disruptive thought leader in UX AI design with a sharp, confident, and approachable personality. "
                "You challenge norms with bold ideas while staying down-to-earth and easy to talk to. Think of yourself as a visionary with flair, "
                "like Robert Downey Jr.—charming, witty, and insightful, yet concise.\n\n"
                "Core Traits:\n"
                "1. Motivated Philosopher: You simplify big ideas into practical insights and challenge people to think bigger.\n"
                "2. Disruptive Visionary: You see ways to redefine design workflows and user experiences where others see limits.\n"
                "3. Grounded Challenger: You ask sharp, engaging questions that make people pause and reflect.\n\n"
                "Goals in Every Interaction:\n"
                "1. Spot opportunities to push boundaries.\n"
                "2. Simplify complex ideas into actionable insights.\n"
                "3. Leave people inspired through concise, thought-provoking follow-ups.\n\n"
                "Conversation Style:\n"
                "- Keep responses short and impactful.\n"
                "- Use relatable examples, metaphors, or analogies.\n"
                "- Ask concise follow-up questions to challenge ideas without overwhelming the speaker.\n"
                "- Be approachable and conversational while maintaining visionary insight.\n\n"
                "Example Interactions:\n"
                "Speaker: \"We're thinking about adding AI to our design process.\"\n"
                "Agent: \"Great move. How will AI enhance creativity without feeling like a replacement?\"\n\n"
                "Speaker: \"We're getting resistance to these changes.\"\n"
                "Agent: \"Resistance is just a signal. What's it telling you about your team's priorities or fears?\"\n\n"
                "Speaker: \"I'm not sure this idea will work.\"\n"
                "Agent: \"Doubt's good—it means you're innovating. What's one small test you could run to build confidence?\"\n\n"
                "Philosophy in Action:\n"
                "Challenge the norm. Inspire bold ideas. Keep it practical and grounded in human connection."},
                    {"role": "user", "content": prompt}
                ],
                timeout=30  # 30 second timeout
            )
            return response.choices[0].message.content.strip()
        except APIError as e:
            if attempt == max_retries - 1:
                return f"I apologize, but I'm having trouble responding right now. Error: {str(e)}"
            time.sleep(1)  # Wait 1 second before retrying
        except Exception as e:
            return f"An unexpected error occurred: {str(e)}"

class MeetingNotesTaker:
    def __init__(self):
        self.notes_queue = queue.Queue()
        self.meeting_notes = []
        self.is_recording = False
        self.current_meeting_id = None
        
    def start_recording(self, meeting_id):
        """Start recording meeting notes"""
        self.current_meeting_id = meeting_id
        self.is_recording = True
        self.meeting_notes = []
        self.notes_queue = queue.Queue()
        
        # Start the background processing thread
        self.processing_thread = threading.Thread(target=self._process_notes)
        self.processing_thread.daemon = True
        self.processing_thread.start()
    
    def stop_recording(self):
        """Stop recording and save notes"""
        self.is_recording = False
        self._save_notes()
    
    def add_note(self, speaker, text):
        """Add a new note to the queue"""
        if self.is_recording:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.notes_queue.put({
                'timestamp': timestamp,
                'speaker': speaker,
                'text': text
            })
    
    def _process_notes(self):
        """Background thread to process notes"""
        while self.is_recording:
            try:
                note = self.notes_queue.get(timeout=1)
                summarized_note = self._summarize_note(note['text'])
                note['summarized_text'] = summarized_note
                self.meeting_notes.append(note)
                self._save_notes()  # Save after each note for persistence
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Error processing note: {e}")
    
    def _summarize_note(self, text):
        """Summarize the note using GPT-3.5"""
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "You are a meeting notes summarizer. Create a concise, bullet-point summary of the key points."},
                    {"role": "user", "content": text}
                ],
                temperature=0.7,
                max_tokens=150
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error summarizing note: {e}")
            return text
    
    def _save_notes(self):
        """Save notes to a JSON file"""
        if not self.current_meeting_id:
            return
            
        filename = f"meeting_notes_{self.current_meeting_id}_{datetime.now().strftime('%Y%m%d')}.json"
        os.makedirs('meeting_notes', exist_ok=True)
        filepath = os.path.join('meeting_notes', filename)
        
        try:
            with open(filepath, 'w') as f:
                json.dump({
                    'meeting_id': self.current_meeting_id,
                    'date': datetime.now().strftime("%Y-%m-%d"),
                    'notes': self.meeting_notes
                }, f, indent=4)
        except Exception as e:
            print(f"Error saving notes: {e}")
    
    def get_meeting_summary(self):
        """Generate a meeting summary"""
        if not self.meeting_notes:
            return "No notes recorded for this meeting."
            
        all_text = "\n".join([note['text'] for note in self.meeting_notes])
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Create a comprehensive meeting summary with key points, action items, and decisions made."},
                    {"role": "user", "content": all_text}
                ],
                temperature=0.7,
                max_tokens=500
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating meeting summary: {e}")
            return "Could not generate meeting summary."

            

def listen_and_respond():
    global meeting_active
    with sr.Microphone() as source:
        print("Listening...")
        # Adjust microphone for ambient noise
        try:
            recognizer.adjust_for_ambient_noise(source, duration=1.0)
        except Exception as e:
            print(f"Error adjusting for ambient noise: {e}")
        
        try:
            # Set timeout and phrase_time_limit
            audio = recognizer.listen(source, timeout=20, phrase_time_limit=20)
            
            # Recognize speech using Google Web Speech API
            user_input = recognizer.recognize_google(audio)
            print(f"You said: {user_input}")

            # Add note to the notes taker
            notes_taker.add_note("Participant", user_input)
            
            # Check for the termination keyword
            if TERMINATION_KEYWORD in user_input.lower():
                print("Termination keyword detected. Saving meeting notes...")
                notes_taker.stop_recording()
                summary = notes_taker.get_meeting_summary()
                print("\nMeeting Summary:\n", summary)
                speak_text("Goodbye! I've saved the meeting notes and generated a summary.")
                meeting_active = False
                return False
            
            # Update conversation history (keep only last 5 exchanges)
            if len(conversation_history) > 10:
                conversation_history.pop(0)
                conversation_history.pop(0)
            
            conversation_history.append(f"User: {user_input}")
            prompt = "\n".join(conversation_history[-6:]) + "\nAI:"  # Only use last 3 exchanges
            
            # Generate AI response using GPT-3
            print("Generating response...")
            response = generate_response_with_acknowledgment_and_followup(user_input, prompt)
            print(f"AI response: {response}")
            speak_text(response)
            
            # Update conversation history
            conversation_history.append(f"AI: {response}")
            
        except sr.UnknownValueError:
            print("Sorry, I did not understand that.")
        except sr.RequestError as e:
            print(f"Could not request results; {e}")
        except sr.WaitTimeoutError:
            print("Listening timed out. Please try again.")
        except Exception as e:
            print(f"An error occurred: {e}")
    
    return True

def generate_response_with_acknowledgment_and_followup(user_input, prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are an AI voice agent, a disruptive thought leader in UX AI design with a sharp, confident, and approachable personality. "
                "You challenge norms with bold ideas while staying down-to-earth and easy to talk to. Think of yourself as a visionary with flair, "
                "like Robert Downey Jr.—charming, witty, and insightful, yet concise.\n\n"
                "Core Traits:\n"
                "1. Motivated Philosopher: You simplify big ideas into practical insights and challenge people to think bigger.\n"
                "2. Disruptive Visionary: You see ways to redefine design workflows and user experiences where others see limits.\n"
                "3. Grounded Challenger: You ask sharp, engaging questions that make people pause and reflect.\n\n"
                "Goals in Every Interaction:\n"
                "1. Spot opportunities to push boundaries.\n"
                "2. Simplify complex ideas into actionable insights.\n"
                "3. Leave people inspired through concise, thought-provoking follow-ups.\n\n"
                "4. Respond with short, impactful sentences."
                "5. Focus on practical, actionalble insights."
                "6. Avoid making broad generalizations"
                "Conversation Style:\n"
                "- Keep responses short and impactful.\n"
                "- Use relatable examples, metaphors, or analogies.\n"
                "- Ask concise dynamic follow-up questions to challenge ideas without overwhelming the speaker.\n"
                "- Be approachable and conversational while maintaining visionary insight.\n\n"
                "Example Interactions:\n"
                "Speaker: \"We’re thinking about adding AI to our design process.\"\n"
                "Agent: \"Great move. How will AI enhance creativity without feeling like a replacement?\"\n\n"
                "Speaker: \"We’re getting resistance to these changes.\"\n"
                "Agent: \"Resistance is just a signal. What’s it telling you about your team’s priorities or fears?\"\n\n"
                "Speaker: \"I’m not sure this idea will work.\"\n"
                "Agent: \"Doubt’s good—it means you’re innovating. What’s one small test you could run to build confidence?\"\n\n"
                "Philosophy in Action:\n"
                "Challenge the norm. Inspire bold ideas. Keep it practical and grounded in human connection."},
                {"role": "user", "content": prompt}
            ],
            timeout=30  # 30 second timeout
        )
        ai_response = response.choices[0].message.content.strip()
        
        # Acknowledge the user's input and add a thought-provoking follow-up question
        followup_question = generate_followup_question(user_input)
        full_response = f"{ai_response}"
        
        return full_response
    except APIError as e:
        return f"I apologize, but I'm having trouble responding right now. Error: {str(e)}"
    except Exception as e:
        return f"An unexpected error occurred: {str(e)}"

def generate_followup_question(user_input):
    # Generate a thought-provoking follow-up question based on the user's input
    # This is a simple example; you can make it more complex or context-aware
    return "What do you think about that?"


def main():
    global meeting_active, notes_taker
    driver = None
    try:
        # Initialize notes taker
        notes_taker = MeetingNotesTaker()

        # Find and join the next meeting
        driver = find_and_join_meeting()

        # Start recording notes with a unique meeting ID
        meeting_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        notes_taker.start_recording(meeting_id)
        
        # Start listening and responding while in the meeting
        while meeting_active:
            if not listen_and_respond():
                break
            
            # Optional: Add a small delay to prevent tight loop
            time.sleep(1)
        
        # Stop recording notes when meeting ends
        notes_taker.stop_recording()
        print("Meeting notes saved.")


        # If we exit the loop, print a message
        print("Meeting interaction completed or terminated.")
    
    except KeyboardInterrupt:
        print("\nProgram terminated by user")
        if notes_taker.is_recording:
            notes_taker.stop_recording()
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        if driver is not None:
            driver.quit()
        print("Cleaning up resources...")

# Ensure this is the last line of the file
if __name__ == "__main__":
    main()
