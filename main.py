from speech_recognition import recognize_speech
from text_to_speech import speak
from ai_agent import generate_response

def main():
    print("AI Voice Agent is running...")
    while True:
        user_input = recognize_speech()
        if user_input:
            response = generate_response(user_input)
            print(f"AI Agent: {response}")
            speak(response)

if __name__ == "__main__":
    main()
