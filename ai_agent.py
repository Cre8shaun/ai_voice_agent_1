from transformers import pipeline

# Initialize the chatbot model
chatbot = pipeline("conversational", model="microsoft/DialoGPT-medium")

def generate_response(user_input):
    conversation = chatbot(user_input)
    response = conversation[0]['generated_text']
    return response
