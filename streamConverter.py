from converter import Converter
import time
import os
from tg_bot import send_tg

def get_file_size(file_path):
    return os.path.getsize(file_path)


def convert_stream_to_mp4(ts_file_path):
    # ffmpeg_path = 'C:\\Program Files\\FFmpeg\\bin\\ffmpeg.exe'
    # ffprobe_path = 'C:\\Program Files\\FFmpeg\\bin\\ffprobe.exe'
    conv = Converter()

    # Get the size and duration of the original TS file
    info = conv.probe(ts_file_path)
    print(info)
    original_size = round(get_file_size(ts_file_path) / (1024 * 1024 * 1024) , 2)  # Convert bytes to gigabytes
    duration = round(info.format.duration, 2)

    send_tg(f"File: {ts_file_path} \n Original Size: {original_size} GB, Duration: {duration} seconds")
    # Derive MP4 file path from TS file path
    mp4_file_path = ts_file_path.replace('.ts', '.mp4')

    print("Stream is converting to mp4, please wait...")
    start_time = time.time()
    try:
        # Start conversion process
        convert = conv.convert(ts_file_path, mp4_file_path, {
            'format': 'mp4',
            'audio': {
                'codec': 'aac',
                'bitrate': 192,
                'samplerate': 48000,
                'channels': 2
            },
            'video': {
                'codec': 'copy'
            }})

        # Wait for conversion to complete without printing each timecode
        for _ in convert:
            pass
        
        conversion_time = time.time() - start_time
        new_size_bytes = get_file_size(mp4_file_path)
        new_size_gb = new_size_bytes / (1024 * 1024 * 1024)  # Convert bytes to gigabytes

        # Round conversion time to 2 decimal places and file size to 2 decimal places
        formatted_time = round(conversion_time, 2)
        formatted_size_gb = round(new_size_gb, 2)

        send_tg(f"Converting finished in {formatted_time} seconds.\nNew MP4 Size: {formatted_size_gb} GB")

        # Delete the original TS file
        os.remove(ts_file_path)
        print(f"Deleted original file: {ts_file_path}")
        print("Converting is finished, you may continue.")
    except Exception as e:
        print(f"Error during conversion: {e}")
