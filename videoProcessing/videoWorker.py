import json
import os
import subprocess
import sys
import time
import logging

from datetime import datetime, timedelta
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_connection import create_connection
from tg_bot import send_tg

logging.basicConfig(filename='C:\\OpenServer\\twitch\\automatic-twitch-recorder\\daemon.log',
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')

def insert_video(video_path, chat_path):
    """
    Create a new video record in the videos table.
    :param video_path: Path to the video file
    :param chat_path: Path to the chat file
    """
    database = r"mp4_processing.db"
    conn = create_connection(database)
    
    if conn is not None:
        sql = ''' INSERT INTO videos(file_path, chat_file_path)
                  VALUES(?,?) '''
        cur = conn.cursor()
        cur.execute(sql, (video_path, chat_path))
        conn.commit()
        last_id = cur.lastrowid
        conn.close()
        return last_id
    else:
        print("Error! cannot create the database connection.")
        return None
    
def delete_video_record(video_path):
    database = r"mp4_processing.db"
    conn = create_connection(database)
    if conn is not None:
        cursor = conn.cursor()
        try:
            # Assuming 'file_path' is the column name in the 'videos' table
            cursor.execute("DELETE FROM videos WHERE file_path = ?", (video_path,))
            conn.commit()
        except Exception as e:
            send_tg(f"Failed to delete record for video {video_path}: {e}", True)
        finally:
            conn.close()
    else:
        send_tg("Failed to connect to the database for deleting video record", True)

    
    # Function to read configuration file
def read_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as file:
                return json.load(file)
        except json.JSONDecodeError as e:
            print(f"Error reading configuration file: {e}")
            exit()
    else:
        print("Configuration file not found.")
        exit()

def timestamp_to_ffmpeg_format(timestamp):
    return str(timestamp).split('.')[0]  # Format as HH:MM:SS

def microseconds_to_datetime(microseconds):
    return datetime.utcfromtimestamp(microseconds / 1e6)

def microseconds_to_seconds(microseconds):
    return microseconds / 1e6

def timedelta_to_hhmmss(timedelta_obj):
    total_seconds = int(timedelta_obj.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}"

def calculate_duration_from_start(video_start_time, event_time):
    return max(event_time - video_start_time, 0)  # Ensure non-negative


def save_chat_messages(timeframe, clip_path):
    chat_messages = list(timeframe['messages'])
    with open(f"{os.path.splitext(clip_path)[0]}_chat.json", 'w') as chat_file:
        json.dump(chat_messages, chat_file, indent=4)

def slice_video(video_path, start_time, end_time, clip_path):
    try:
        ffmpeg_cmd = ["ffmpeg", "-i", video_path, "-ss", start_time, "-to", end_time, "-c", "copy", clip_path]
        subprocess.run(ffmpeg_cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        send_tg(f"Error slicing video: {e}", True)
        print(f"Error slicing video: {e}")
        return False
    
def get_video_duration(video_path):
# Returns the duration of the video in seconds.
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                    "format=duration", "-of",
                                    "default=noprint_wrappers=1:nokey=1", video_path],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return float(result.stdout)
    except Exception as e:
        print(f"Error getting video duration: {e}")
        send_tg(f"Error getting video duration: {e}", True)
        return None
    
def process_timeframe(video_path, video_start_time, timeframe, clip_dir, config):
    logging.info("-------------------  START ----------------")
    logging.info(f"timeframe start {timeframe['start']} and timeframe end {timeframe['end']}")

    # Get the total duration of the video
    video_total_duration = get_video_duration(video_path)
    logging.info(f"Video total duration {video_total_duration}")

    if video_total_duration is None:
        print("Error obtaining video duration for", video_path)
        return

    video_total_duration = timedelta(seconds=video_total_duration)
    logging.info(f"Video total duration timedelta {video_total_duration}")

    video_total_duration_seconds = get_video_duration(video_path)
    if video_total_duration_seconds is None:
        print("Error obtaining video duration for", video_path)
        return

    video_total_duration_td = timedelta(seconds=video_total_duration_seconds)
    logging.info(f"Video total duration timedelta {video_total_duration_td}")
    # Convert timestamps from microseconds to seconds
    logging.info("Convert timestamps from microseconds to seconds")
    start_time = microseconds_to_seconds(timeframe['start'])
    end_time = microseconds_to_seconds(timeframe['end'])
    logging.info(f"start_time {start_time} and end_time {end_time}")


    # Calculate duration from the start of the video
    logging.info("Calculate duration from the start of the video")
    start_duration = calculate_duration_from_start(video_start_time, start_time)
    end_duration = calculate_duration_from_start(video_start_time, end_time)
    logging.info(f"start_duration {start_duration} and end_duration {end_duration}")

    # Convert to timedelta
    logging.info("Convert to timedelta")
    start_duration = timedelta(seconds=start_duration)
    end_duration = timedelta(seconds=end_duration)
    logging.info(f"start_duration {start_duration} and end_duration {end_duration}")

    # Adjust timeframe duration
    logging.info("Adjust timeframe duration")
    duration = end_duration - start_duration
    if duration < timedelta(minutes=1.5):
        extend_each_side = (timedelta(minutes=1.5) - duration) / 2
        start_duration -= extend_each_side
        end_duration += extend_each_side
    elif duration < timedelta(minutes=3):
        extend_each_side = (timedelta(minutes=3) - duration) / 2
        start_duration -= extend_each_side
        end_duration += extend_each_side
    logging.info(f"start_duration {start_duration} and end_duration {end_duration}")
    # Ensure the start and end durations are within the video's duration
    logging.info("Ensure the start and end durations are within the video's duration")
    start_duration = max(timedelta(seconds=0), start_duration)
    # Replace 'video_total_duration' with the total duration of the video in timedelta format
    logging.info("Replace 'video_total_duration' with the total duration of the video in timedelta format")
    end_duration = min(video_total_duration, end_duration)
    logging.info(f"start_duration {start_duration} and end_duration {end_duration}")

    # Format as HH:MM:SS
    logging.info("Format as HH:MM:SS")
    start_timestamp = timedelta_to_hhmmss(start_duration)
    end_timestamp = timedelta_to_hhmmss(end_duration)
    logging.info(f"start_timestamp {start_timestamp} and end_timestamp {end_timestamp}")
    logging.info("-------------------  END ----------------")

    clip_path = os.path.join(clip_dir, f"clip_{datetime.now().strftime('%Y%m%d%H%M%S')}.mp4")
    success = slice_video(video_path, start_timestamp, end_timestamp, clip_path)

    if success and config.get('save_chat_messages', False):
        save_chat_messages(timeframe, clip_path)

    return success

def make_clips(video_path, timeframes_json, config_path):
    start_time = time.time()
    config = read_config(config_path)
    video_dir, video_filename = os.path.split(video_path)
    # Split the filename and extract the date part
    parts = video_filename.split(' - ')
    if len(parts) >= 2:
        video_date_str = parts[0]
        streamer_name = parts[1].split('.')[0]  # Assuming the streamer's name is the second part
    else:
        print("Error: Invalid video filename format.")
        return
    
    send_tg(f"Starting processing for streamer: {streamer_name}")
    file_size = os.path.getsize(video_path) / (1024 * 1024 * 1024)   # Size in GB
    send_tg(f"File size: {file_size:.2f} GB")

    try:
        video_date = datetime.strptime(video_date_str, "%Y-%m-%d %H.%M.%S")

    except ValueError as e:
        print(f"Error parsing the date from the video filename: {e}")
        return

    base_clip_dir = config.get('clips_storage_path', os.path.join(video_dir, "Clips"))
    clip_dir = os.path.join(base_clip_dir, streamer_name, video_date.strftime("%Y-%m-%d"))
    os.makedirs(clip_dir, exist_ok=True)

    delete_if_not_interesting = config.get('deleteNotInteresting', False)

    with open(timeframes_json, 'r') as file:
        timeframes = json.load(file)
        send_tg(f"Total timeframes from chat: {len(timeframes)}")
            # Check if there are no interesting segments
        if delete_if_not_interesting and not timeframes:
            send_tg(f"No interesting segments found in chat for video {video_path}. Deleting files.", True)
            # os.remove(video_path)
            # os.remove(timeframes_json)  # Assuming you want to delete the segments JSON file too

            # Delete database record
            # delete_video_record(video_path)

            return  # Exit the function as there is nothing more to process

        video_start_time = 0  # Assuming the video starts at 0 seconds
        success_count = 0
        failure_count = 0
        if config.get('process_all_at_once', False):
            # Process all timeframes in a single call
            for timeframe in timeframes:
                process_timeframe(video_path, video_start_time, timeframe, clip_dir, config)
        else:
            # Process each timeframe individually
            for timeframe in timeframes:
                success = process_timeframe(video_path, video_start_time, timeframe, clip_dir, config)
                if success:
                    success_count += 1
                else:
                    failure_count += 1

    processing_time = time.time() - start_time
    send_tg(f"Processing completed in {processing_time:.2f} seconds", False)
    # Log the success and failure counts
    send_tg(f"Processing results: {success_count} successful, {failure_count} failed", False)

    # Delete original MP4 file if configured
    if config.get('delete_original', False) and failure_count == 0:
        try:
            os.remove(video_path)
        except OSError as e:
            print(f"Error deleting original video file: {e}")

def construct_segments_json_path(video_path):
    base_dir = "C:\\OpenServer\\twitch\\automatic-twitch-recorder"
    chat_segments_dir = os.path.join(base_dir, "chatSegments")

    # Extract the base filename without the .mp4 extension
    base_filename = os.path.splitext(os.path.basename(video_path))[0]

    # Construct the new filename for the segments JSON
    segments_json_filename = f"{base_filename}chatsegments.json"

    return os.path.join(chat_segments_dir, segments_json_filename)



def main():
    if len(sys.argv) != 3:
        print("Usage: python videoProcessor.py <video_path> <config_path>")
        sys.exit(1)

    video_path = sys.argv[1]
    timeframes_json = construct_segments_json_path(video_path)
    config_path = sys.argv[2]

    make_clips(video_path, timeframes_json, config_path)

if __name__ == "__main__":
    main()