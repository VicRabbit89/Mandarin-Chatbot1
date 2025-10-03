# Mandarin Chatbot Deployment Guide

## 🚀 Quick Deployment to Render (Recommended)

### Prerequisites
- GitHub account
- OpenAI API key
- Render account (free at render.com)

### Step 1: Push to GitHub
```bash
# Navigate to your project directory
cd "/Users/victoria/CascadeProjects/Mandarin_Chatbot Retry/CascadeProjects/windsurf-project"

# Initialize git (if not already done)
git init
git add .
git commit -m "Deploy Mandarin chatbot with Emily improvements"

# Push to GitHub (create a new repo first on github.com)
git remote add origin https://github.com/YOUR_USERNAME/mandarin-chatbot.git
git branch -M main
git push -u origin main
```

### Step 2: Deploy on Render
1. Go to [render.com](https://render.com) and sign up/login
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Render will auto-detect the `render.yaml` configuration
5. Set environment variables:
   - `OPENAI_API_KEY`: Your OpenAI API key
   - `ENV`: production
   - `PORT`: 10000 (Render default)

### Step 3: Configure Domain (Optional)
- Use the provided `.onrender.com` URL
- Or connect your custom domain in Render settings

## 🎯 All Your Improvements Are Included

✅ **Emily Fixes:**
- Waits for students to ask questions first
- Limited to exactly 3 questions per conversation
- Smart family logic (won't ask about non-existent siblings)
- Help messages only show once until student responds
- Contextually relevant questions only

✅ **Matching Activity Improvements:**
- Enhanced visual feedback with green styling and checkmarks
- Progress tracking with visual progress bar
- Bidirectional selection (can select from either column first)
- Better organization with "Completed Matches" section

✅ **UI Improvements:**
- Removed broken pause button
- Cleaner interface
- Better visual indicators

## 🔧 Alternative Deployment Options

### Railway
1. Go to [railway.app](https://railway.app)
2. Connect GitHub repo
3. Set `OPENAI_API_KEY` environment variable
4. Deploy automatically

### Heroku
1. Install Heroku CLI
2. `heroku create your-app-name`
3. `heroku config:set OPENAI_API_KEY=your_key`
4. `git push heroku main`

### PythonAnywhere
1. Upload files to PythonAnywhere
2. Set up WSGI configuration
3. Install requirements: `pip install -r requirements.txt`
4. Configure environment variables

## 🔐 Security Notes

- Never commit your `.env` file with real API keys
- Set `OPENAI_API_KEY` as environment variable in hosting platform
- Use HTTPS in production (Render provides this automatically)
- Consider rate limiting for production use (already configured)

## 📊 Monitoring

- Check Render logs for any errors
- Monitor OpenAI API usage in OpenAI dashboard
- Test all features after deployment:
  - Emily roleplay conversations
  - Matching activities
  - Voice input/output
  - Help message timing

## 🎉 Your Improved Mandarin Chatbot is Ready!

Students will now experience:
- Better Emily behavior (student-led conversations)
- Clearer matching activities
- No more repeated help messages
- Enhanced visual feedback

Perfect for your next pilot study! 🚀
