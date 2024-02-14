import json
import re
import os
import sys
import logging
from datetime import datetime, timedelta

filename = os.path.join(os.path.normpath((os.path.dirname(os.path.abspath(__file__)))), "chatProcessor.log")
logging.basicConfig(filename=filename,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')
# Constraints
KEYWORDS = {"ахax", "уфф", "pog", "wow," "lul", "lol", "xdd", "clip" , "клип"}  # Russian and English keywords
SEGMENT_LENGTH = timedelta(seconds=10)  # Segment length
FREQUENCY_THRESHOLD = 15  # Minimum number of keyword occurrences to consider a segment interesting
LAUGHTER_PATTERN = re.compile(r'\b[зфыхаъпha]{2,}\b', re.IGNORECASE) # Laughter pattern regex 
def timestamp_to_datetime(timestamp):
    # Assuming the timestamp is in microseconds since the Unix epoch
    return datetime.utcfromtimestamp(timestamp / 1e6)

def timestamp_to_relative_seconds(timestamp, first_timestamp):
    """Converts an absolute timestamp to seconds relative to the first timestamp."""
    return (timestamp_to_datetime(timestamp) - timestamp_to_datetime(first_timestamp)).total_seconds()

def repair_json_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            # Attempt to parse each line as JSON, keep only valid lines
            valid_lines = [line for line in lines if line.strip()]
            if valid_lines:
                last_line = valid_lines[-1]
                try:
                    json.loads(last_line)
                except json.JSONDecodeError:
                    # Remove the last line if it's invalid
                    valid_lines.pop()
            # Rewrite the file with valid JSON lines
            with open(file_path, 'w', encoding='utf-8') as file:
                file.writelines(valid_lines)
    except Exception as e:
        print(f"Error repairing JSON file: {e}")


def segment_chat(chat_log, segment_length):
    segments = []
    start = None
    current_segment = []

    for message in chat_log:
        timestamp = message['timestamp']  # Assuming the timestamp is already in seconds
        if start is None:
            start = timestamp
        if timestamp - start >= segment_length.total_seconds():
            segments.append(current_segment)
            current_segment = []
            start = timestamp
        current_segment.append(message)
    if current_segment:
        segments.append(current_segment)
    return segments

def find_interesting_segments(chat_segments, keywords, frequency_threshold):
    interesting_segments = []
    for segment in chat_segments:
            keyword_count = 0
            for message in segment:
                message_text = message['message'].lower()
                if any(keyword in message_text for keyword in keywords):
                    keyword_count += 1
                elif LAUGHTER_PATTERN.search(message_text):
                    keyword_count += 1
            if keyword_count >= frequency_threshold:
                interesting_segments.append(segment)
    return interesting_segments

def merge_consecutive_segments(segments):
    merged_segments = []
    if not segments:
        return merged_segments
    current_segment = segments[0]

    for segment in segments[1:]:
        if segment[0]['timestamp'] - current_segment[-1]['timestamp'] <= SEGMENT_LENGTH.total_seconds():
            current_segment.extend(segment)
        else:
            merged_segments.append(current_segment)
            current_segment = segment

    if current_segment:
        merged_segments.append(current_segment)
    return merged_segments

def process_chat_log(file_path):
    chat_log = []
    first_timestamp = None
    with open(file_path, 'r', encoding='utf-8') as file:
        for line in file:
            try:
                message = json.loads(line.strip())
                if first_timestamp is None:
                    first_timestamp = message['timestamp']
                 # Normalize timestamp
                # message['timestamp'] -= first_timestamp
                # Adjust timestamp to be relative to the first timestamp
                message['timestamp'] = timestamp_to_relative_seconds(message['timestamp'], first_timestamp)
                chat_log.append(message)
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON: {e}")
    return chat_log, first_timestamp

def save_to_json(segments, output_file, first_timestamp):
    with open(output_file, 'w', encoding='utf-8') as file:
        json.dump([{"start": seg[0]['timestamp'] * 1e6, "end": seg[-1]['timestamp'] * 1e6, "messages": seg} for seg in segments], file, indent=4)


def process_and_save_chat_segments(chat_log_path,segment_length, keywords, frequency_threshold):
    """
    Processes a chat log file and saves the interesting segments to a JSON file.

    Parameters:
    chat_log_path (str): Path to the chat log file.
    output_json_path (str): Path to the output JSON file.
    segment_length (timedelta): Length of each chat segment.
    keywords (set): Set of keywords to look for in chat messages.
    frequency_threshold (int): Minimum number of keyword occurrences for a segment to be interesting.
    """
    # Repair json if it's coropted
    repair_json_file(chat_log_path)
    # Process the chat log
    chat_log, first_timestamp = process_chat_log(chat_log_path)

    # Segment the chat log
    chat_segments = segment_chat(chat_log, segment_length)

    # Find interesting segments based on keywords and frequency
    interesting_segments = find_interesting_segments(chat_segments, keywords, frequency_threshold)

    # Merge consecutive segments
    merged_segments = merge_consecutive_segments(interesting_segments)

    # Construct the output directory and file name
    _, chat_filename = os.path.split(chat_log_path)
    base_filename, _ = os.path.splitext(chat_filename)
    
    # Set the output directory to be C:\OpenServer\twitch\automatic-twitch-recorder\chat_segments\
    output_dir = os.path.join(os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")), "chatSegments")
    os.makedirs(output_dir, exist_ok=True)  # Create the directory if it doesn't exist
    output_json_path = os.path.join(output_dir, f"{base_filename}segments.json")

    # Save the interesting segments to a JSON file
    save_to_json(merged_segments, output_json_path, first_timestamp)

def main():
    if len(sys.argv) != 2:
        print("Usage: python chatProcessor.py <chat_log_path>")
        sys.exit(1)

    chat_log_path = sys.argv[1]
    process_and_save_chat_segments(chat_log_path, SEGMENT_LENGTH, KEYWORDS, FREQUENCY_THRESHOLD)

if __name__ == "__main__":
    main()

