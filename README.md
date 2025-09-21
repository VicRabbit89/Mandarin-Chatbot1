# Mandarin Learning Chatbot

An interactive chatbot that helps users learn Mandarin Chinese through conversation. The chatbot uses OpenAI's GPT-4 to provide natural language responses in both English and Chinese, complete with pinyin pronunciation guides.

## Features

- Interactive chat interface for practicing Mandarin
- Responses in both English and Chinese (simplified characters)
- Pinyin pronunciation guide for Chinese characters
- Context-aware conversations
- Quick suggestion buttons for common phrases
- Responsive design that works on desktop and mobile

## Setup

1. Clone this repository
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create a `.env` file in the root directory and add your OpenAI API key:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   ```
4. Run the application:
   ```bash
   python app.py
   ```
5. Open your browser and navigate to `http://localhost:5000`

## Usage

1. Type your message in the input field and press Enter or click Send
2. The chatbot will respond in both English and Chinese
3. Use the quick suggestion buttons to practice common phrases
4. The chatbot will correct your mistakes and help you improve

## Requirements

- Python 3.7+
- OpenAI API key
- Internet connection

## Deployment

For production deployment, you can use Gunicorn with:

```bash
gunicorn app:app
```

## Future Improvements

- Add voice recognition for speaking practice
- Include character writing practice
- Add progress tracking
- Implement spaced repetition for vocabulary
- Add more interactive exercises

## License

This project is open source and available under the MIT License.
