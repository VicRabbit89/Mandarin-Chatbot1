# Pilot Study Data Collection Guide

This document explains how to collect and analyze user data during your Mandarin chatbot pilot study.

## What Data is Collected

The app automatically collects anonymized usage data when `ENABLE_ANALYTICS=true`:

### 1. **Page Visits**
- Which pages users visit (home, roleplay, matching)
- User agent (browser/device info)
- Session tracking

### 2. **Roleplay Interactions**
- Unit selection preferences
- Message length patterns
- Student name usage
- Conversation flow patterns

### 3. **Matching Activity Performance**
- Unit difficulty preferences
- Number of pairs attempted
- Activity completion patterns

### 4. **User Feedback**
- Star ratings (1-5 scale)
- Feedback categories (general, bug, feature, usability)
- Written feedback messages
- Page-specific feedback

## Configuration

### Environment Variables
Add these to your `.env` file or Render environment variables:

```bash
# Enable data collection
ENABLE_ANALYTICS=true

# Sample rate (1.0 = 100%, 0.5 = 50%, etc.)
ANALYTICS_SAMPLE_RATE=1.0
```

### Privacy Settings
- **IP addresses**: Only hashed, never stored in plain text
- **Messages**: Truncated to first 100 characters for privacy
- **Sessions**: Tracked by random session ID, not personal info
- **Storage**: Local files only, never sent to third parties

## Data Storage

Data is stored in `analytics/pilot_data_YYYYMMDD.jsonl` files:

```
analytics/
├── pilot_data_20241201.jsonl
├── pilot_data_20241202.jsonl
└── pilot_data_20241203.jsonl
```

Each line is a JSON object:
```json
{
  "timestamp": "2024-12-01T10:30:00Z",
  "event_type": "roleplay_turn",
  "user_id": "anonymous",
  "session_id": "abc12345",
  "data": {
    "unit_id": "unit1",
    "message_length": 25,
    "has_student_name": true
  },
  "ip_hash": 1234567890
}
```

## Feedback Widget

A floating feedback button appears on all pages. Users can:
- Rate their experience (1-5 stars)
- Select feedback type (general, bug, feature, usability)
- Write detailed feedback (up to 1000 characters)
- Submit page-specific feedback

## Data Analysis

### Quick Analysis
Run the included analysis script:

```bash
# Analyze all data
python analyze_pilot_data.py

# Analyze specific date range
python analyze_pilot_data.py 20241201-20241207

# Analyze single day
python analyze_pilot_data.py 20241201
```

### Sample Output
```
=== PILOT STUDY SUMMARY REPORT ===
Data period: 2024-12-01 to 2024-12-07
Total events: 1,247
Unique sessions: 89
Most active day: 2024-12-03 (312 events)

=== USER ENGAGEMENT ANALYSIS ===
Total sessions: 89
Average events per session: 14.0
Max events in a session: 45

Event distribution:
  roleplay_turn: 456
  matching_attempt: 234
  page_visit: 178
  user_feedback: 23

=== ROLEPLAY ANALYSIS ===
Unit usage:
  unit1: 234 turns
  unit2: 156 turns
  unit3: 66 turns

Message length stats:
  Average: 28.5 characters
  Max: 145 characters
  Min: 3 characters

Student name usage: 67/456 (14.7%)
```

## Key Metrics to Track

### Engagement Metrics
- **Session length**: Average events per session
- **Return usage**: Sessions with multiple page visits
- **Feature adoption**: Roleplay vs. matching usage
- **Unit progression**: Which units are most/least popular

### Learning Effectiveness
- **Message complexity**: Average message length over time
- **Unit completion**: Progression through units
- **Error patterns**: Common mistakes in matching activities
- **Feedback sentiment**: User satisfaction ratings

### Technical Performance
- **Error rates**: Failed API calls or timeouts
- **Load times**: Page visit to interaction delays
- **Device usage**: Mobile vs. desktop patterns
- **Browser compatibility**: User agent analysis

## Privacy & Ethics

### Data Minimization
- Only collect data necessary for improvement
- Truncate long messages to protect privacy
- Use session IDs instead of personal identifiers
- Hash IP addresses for abuse prevention only

### User Rights
- Users can opt out by disabling JavaScript
- No personal information is required or stored
- Data is used only for educational improvement
- Clear feedback about data collection in UI

### Data Retention
- Keep pilot data only during study period
- Delete analytics files after analysis complete
- Never share raw data with third parties
- Aggregate findings only in reports

## Deployment Notes

### Render Configuration
Set these environment variables in Render:
```
ENABLE_ANALYTICS=true
ANALYTICS_SAMPLE_RATE=1.0
```

### File Permissions
The app creates the `analytics/` directory automatically. Ensure write permissions in production.

### Monitoring
Check logs for analytics warnings:
```bash
# In Render logs, look for:
WARNING Analytics logging failed: [error details]
```

## Analysis Tips

### Pre-Pilot
1. Test data collection locally with `ENABLE_ANALYTICS=true`
2. Verify feedback widget appears on all pages
3. Submit test feedback to confirm endpoint works
4. Run analysis script on test data

### During Pilot
1. Monitor daily event counts for participation
2. Check for error patterns in logs
3. Review feedback submissions regularly
4. Adjust `ANALYTICS_SAMPLE_RATE` if too much data

### Post-Pilot
1. Run full analysis on all collected data
2. Export key findings to spreadsheet/report
3. Identify top improvement priorities
4. Plan A/B tests for next iteration

## Troubleshooting

### No Data Collected
- Check `ENABLE_ANALYTICS=true` in environment
- Verify `analytics/` directory is writable
- Look for JavaScript errors in browser console
- Confirm users are actually using the app

### Feedback Widget Not Appearing
- Check browser JavaScript console for errors
- Verify `/static/feedback-widget.js` is accessible
- Ensure script tag is in HTML `<head>` section
- Test with different browsers/devices

### Analysis Script Errors
- Install required Python packages: `json`, `pathlib`, `collections`
- Check `analytics/` directory exists and has `.jsonl` files
- Verify file permissions for reading analytics data
- Run with `python -u analyze_pilot_data.py` for unbuffered output

## Next Steps

After pilot analysis, consider:

1. **Prompt Engineering**: Update system messages based on user patterns
2. **Content Updates**: Add vocabulary/scenarios users struggled with
3. **UI Improvements**: Fix usability issues from feedback
4. **A/B Testing**: Test different teaching approaches
5. **OpenAI Fine-tuning**: Train custom model on successful interactions

For questions about data collection or analysis, refer to the main README or check the application logs.
