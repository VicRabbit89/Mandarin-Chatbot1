#!/usr/bin/env python3
"""
Pilot Study Data Analysis Script

Run this script to analyze user interaction data collected during the pilot study.
Usage: python analyze_pilot_data.py [date_range]

Example: python analyze_pilot_data.py 20241201-20241207
"""

import json
import os
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timedelta
import sys

def load_analytics_data(analytics_dir, date_filter=None):
    """Load all analytics data from JSONL files"""
    data = []
    analytics_path = Path(analytics_dir)
    
    if not analytics_path.exists():
        print(f"Analytics directory not found: {analytics_path}")
        return data
    
    for file_path in analytics_path.glob("pilot_data_*.jsonl"):
        # Extract date from filename
        date_str = file_path.stem.replace('pilot_data_', '')
        
        # Apply date filter if specified
        if date_filter and date_str not in date_filter:
            continue
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        data.append(entry)
        except Exception as e:
            print(f"Error reading {file_path}: {e}")
    
    return data

def analyze_user_engagement(data):
    """Analyze user engagement patterns"""
    print("\n=== USER ENGAGEMENT ANALYSIS ===")
    
    # Session analysis
    sessions = defaultdict(list)
    for entry in data:
        sessions[entry['session_id']].append(entry)
    
    session_lengths = [len(events) for events in sessions.values()]
    
    print(f"Total sessions: {len(sessions)}")
    print(f"Average events per session: {sum(session_lengths) / len(session_lengths):.1f}")
    print(f"Max events in a session: {max(session_lengths) if session_lengths else 0}")
    
    # Event type distribution
    event_counts = Counter(entry['event_type'] for entry in data)
    print(f"\nEvent distribution:")
    for event_type, count in event_counts.most_common():
        print(f"  {event_type}: {count}")

def analyze_roleplay_usage(data):
    """Analyze roleplay interaction patterns"""
    print("\n=== ROLEPLAY ANALYSIS ===")
    
    roleplay_events = [e for e in data if e['event_type'] == 'roleplay_turn']
    
    if not roleplay_events:
        print("No roleplay interactions found.")
        return
    
    # Unit popularity
    unit_usage = Counter(e['data'].get('unit_id') for e in roleplay_events)
    print(f"Unit usage:")
    for unit_id, count in unit_usage.most_common():
        print(f"  {unit_id}: {count} turns")
    
    # Message length analysis
    message_lengths = [e['data'].get('message_length', 0) for e in roleplay_events]
    if message_lengths:
        avg_length = sum(message_lengths) / len(message_lengths)
        print(f"\nMessage length stats:")
        print(f"  Average: {avg_length:.1f} characters")
        print(f"  Max: {max(message_lengths)} characters")
        print(f"  Min: {min(message_lengths)} characters")
    
    # Student name usage
    with_names = sum(1 for e in roleplay_events if e['data'].get('has_student_name'))
    print(f"\nStudent name usage: {with_names}/{len(roleplay_events)} ({with_names/len(roleplay_events)*100:.1f}%)")

def analyze_matching_performance(data):
    """Analyze matching activity performance"""
    print("\n=== MATCHING ACTIVITY ANALYSIS ===")
    
    matching_events = [e for e in data if e['event_type'] == 'matching_attempt']
    
    if not matching_events:
        print("No matching attempts found.")
        return
    
    # Unit popularity
    unit_usage = Counter(e['data'].get('unit_id') for e in matching_events)
    print(f"Unit usage:")
    for unit_id, count in unit_usage.most_common():
        print(f"  {unit_id}: {count} attempts")
    
    # Pair count analysis
    pair_counts = [e['data'].get('num_pairs', 0) for e in matching_events]
    if pair_counts:
        avg_pairs = sum(pair_counts) / len(pair_counts)
        print(f"\nPairs per attempt:")
        print(f"  Average: {avg_pairs:.1f}")
        print(f"  Max: {max(pair_counts)}")
        print(f"  Min: {min(pair_counts)}")

def analyze_feedback(data):
    """Analyze user feedback"""
    print("\n=== USER FEEDBACK ANALYSIS ===")
    
    feedback_events = [e for e in data if e['event_type'] == 'user_feedback']
    
    if not feedback_events:
        print("No user feedback found.")
        return
    
    print(f"Total feedback submissions: {len(feedback_events)}")
    
    # Feedback types
    feedback_types = Counter(e['data'].get('feedback_type') for e in feedback_events)
    print(f"\nFeedback types:")
    for fb_type, count in feedback_types.most_common():
        print(f"  {fb_type}: {count}")
    
    # Ratings analysis
    ratings = [e['data'].get('rating') for e in feedback_events if e['data'].get('rating')]
    if ratings:
        avg_rating = sum(ratings) / len(ratings)
        print(f"\nRatings (1-5 scale):")
        print(f"  Average: {avg_rating:.1f}")
        print(f"  Distribution: {Counter(ratings)}")
    
    # Sample feedback messages (first 3, truncated)
    print(f"\nSample feedback:")
    for i, event in enumerate(feedback_events[:3]):
        message = event['data'].get('message', '')[:100]
        print(f"  {i+1}. {message}{'...' if len(event['data'].get('message', '')) > 100 else ''}")

def generate_summary_report(data):
    """Generate a summary report"""
    print("\n" + "="*50)
    print("PILOT STUDY SUMMARY REPORT")
    print("="*50)
    
    if not data:
        print("No data available for analysis.")
        return
    
    # Time range
    timestamps = [datetime.fromisoformat(e['timestamp'].replace('Z', '+00:00')) for e in data]
    start_date = min(timestamps).strftime('%Y-%m-%d')
    end_date = max(timestamps).strftime('%Y-%m-%d')
    
    print(f"Data period: {start_date} to {end_date}")
    print(f"Total events: {len(data)}")
    
    # Unique users (based on session_id as proxy)
    unique_sessions = len(set(e['session_id'] for e in data))
    print(f"Unique sessions: {unique_sessions}")
    
    # Most active day
    daily_counts = Counter(ts.strftime('%Y-%m-%d') for ts in timestamps)
    if daily_counts:
        busiest_day, max_events = daily_counts.most_common(1)[0]
        print(f"Most active day: {busiest_day} ({max_events} events)")

def main():
    """Main analysis function"""
    analytics_dir = Path(__file__).parent / 'analytics'
    
    # Parse date filter from command line
    date_filter = None
    if len(sys.argv) > 1:
        date_range = sys.argv[1]
        if '-' in date_range:
            start_date, end_date = date_range.split('-')
            # Generate all dates in range
            start = datetime.strptime(start_date, '%Y%m%d')
            end = datetime.strptime(end_date, '%Y%m%d')
            date_filter = []
            current = start
            while current <= end:
                date_filter.append(current.strftime('%Y%m%d'))
                current += timedelta(days=1)
        else:
            date_filter = [date_range]
    
    # Load and analyze data
    data = load_analytics_data(analytics_dir, date_filter)
    
    if not data:
        print("No analytics data found. Make sure ENABLE_ANALYTICS=true and users have interacted with the app.")
        return
    
    # Run all analyses
    generate_summary_report(data)
    analyze_user_engagement(data)
    analyze_roleplay_usage(data)
    analyze_matching_performance(data)
    analyze_feedback(data)
    
    print(f"\nAnalysis complete. Analyzed {len(data)} events.")
    print(f"Analytics data location: {analytics_dir}")

if __name__ == '__main__':
    main()
