import os
import logging
import twitch
import db_connection
import subprocess
from tg_bot import send_tg
from main import STREAMERS_FILE

# Configure logging
filename = os.path.join(os.path.normpath((os.path.dirname(os.path.abspath(__file__)))), "processing.log")
logging.basicConfig(filename=filename,
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')
config_path = os.path.join(os.path.join(os.path.normpath((os.path.dirname(os.path.abspath(__file__)))), "videoProcessing"), "config.json")

def load_streamers_from_file(file_path):
    streamers = []
    if not os.path.exists(file_path):
        logging.error("Streamers file not found: %s", file_path)
        return streamers

    with open(file_path, 'r') as file:
        streamers = [line.strip() for line in file if line.strip()]
    return streamers

def fetch_streamer_user_ids(streamer_names):
    user_ids = []
    for streamer_name in streamer_names:
        if user_info := twitch.get_user_info(streamer_name):
            user_ids.append(user_info[0]['id'])
    return user_ids

def are_any_streamers_live(streamer_user_ids):
    if not streamer_user_ids:
        return False

    streams_info = twitch.get_stream_info(*streamer_user_ids)
    return any(streams_info[user_id]['status'] == 'online' for user_id in streamer_user_ids)


def check_live_streams():
    streamers_file_path = os.path.join(os.getcwd(), STREAMERS_FILE) # Update this path
    streamer_names = load_streamers_from_file(streamers_file_path)
    streamer_user_ids = fetch_streamer_user_ids(streamer_names)
    return are_any_streamers_live(streamer_user_ids)

# Now you can call check_live_streams in your process_videos function
def process_videos():
    database_path = "mp4_processing.db"
    conn = db_connection.create_connection(database_path)

    if conn is not None:
        cursor = conn.cursor()
        cursor.execute("SELECT id, file_path, chat_file_path FROM videos WHERE processed = 0 AND id = 2")
        videos = cursor.fetchall()

        for video in videos:
            if check_live_streams():
                logging.info("Live streams are ongoing. Skipping video processing.")
                # return
            video_id, video_path, chat_path = video

            try:
                logging.info("Starting processing for video ID %s", video_id)

                if checkIfFileExistREmoveIfNot(video_path, chat_path, video_id, cursor, conn) != True:
                    continue
                # Construct the path to chatProcessor.py
                chat_processor_path = os.path.join(os.path.dirname(__file__), 'chatProcessing', 'chatProcessor.py')
                # Call chatProcessor script
                subprocess.run(['python3', chat_processor_path, chat_path], check=True)

                # Construct the path to videoWorker.py
                video_worker_path = os.path.join(os.path.dirname(__file__), 'videoProcessing', 'videoWorker.py')

                # Call videoProcessor script with video path and configuration path
                subprocess.run(['python3', video_worker_path, video_path, config_path, chat_path], check=True)


                # Update processed status to 2 (success)
                cursor.execute("UPDATE videos SET processed = 2 WHERE id = ?", (video_id,))
                logging.info("Finished processing for video ID %s", video_id)
            except subprocess.CalledProcessError:
                logging.error("Error processing video ID %s: %s", video_id, e)
                # Update processed status to 1 (failed)
                cursor.execute("UPDATE videos SET processed = 1 WHERE id = ?", (video_id,))

        conn.commit()
        conn.close()
    else:
        logging.error("Failed to connect to the database")

def checkIfFileExistREmoveIfNot(video_path, chat_path, video_id, cursor, conn):
    video_exists = os.path.exists(video_path) and os.path.getsize(video_path) > 0
    chat_exists = os.path.exists(chat_path) and os.path.getsize(chat_path) > 0

    if not video_exists or not chat_exists:
        error_message = f"Missing or empty file for video ID {video_id}: "
        error_message += f"{'Video file' if not video_exists else 'Chat file'}"

        logging.error(error_message)
        send_tg(error_message, True)
        '''
        # Delete video and chat file if they exist
        if os.path.exists(video_path):
            os.remove(video_path)
        if os.path.exists(chat_path):
            os.remove(chat_path)

        # Remove record from database
        cursor.execute("DELETE FROM videos WHERE id = ?", (video_id,))
        conn.commit()
        '''
        cursor.execute("UPDATE videos SET processed = 1 WHERE id = ?", (video_id,))
        conn.commit()
        return False
    return True


def main():
    logging.info("Script started")
    try:
        process_videos()
    except Exception as e:
        logging.exception("An error occurred during processing: %s", e)

if __name__ == "__main__":
    main()