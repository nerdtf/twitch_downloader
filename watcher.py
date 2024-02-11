import datetime
import streamlink
import os
import threading
from utils import get_valid_filename, StreamQualities
from chat_downloader import ChatDownloader
import json
from requests.exceptions import RequestException
from streamConverter import convert_stream_to_mp4
from videoProcessing.videoWorker import insert_video
from tg_bot import send_tg
from multiprocessing import Process


class Watcher:
    streamer_dict = {}
    streamer = ''
    stream_title = ''
    stream_quality = ''
    kill = False
    cleanup = False

    def __init__(self, streamer_dict, download_folder):
        self.streamer_dict = streamer_dict
        self.streamer = self.streamer_dict['user_info']['display_name']
        self.streamer_login = self.streamer_dict['user_info']['login']
        
        # Extracting stream information from streamer_dict
        stream_info = self.streamer_dict.get('stream_info', {})
        self.stream_title = stream_info.get('title', 'No Title')
        self.viewer_count = stream_info.get('viewer_count', 0)
        self.started_at = stream_info.get('started_at', '')
        self.stream_quality = self.streamer_dict['preferred_quality']
        self.download_folder = download_folder

    def quit(self):
        self.kill = True

    def clean_break(self):
        self.cleanup = True

    def watch(self):
        curr_time = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        file_name = curr_time + " - " + self.streamer + " - " + get_valid_filename(self.stream_title) + ".ts"
        directory = self._formatted_download_folder(self.streamer_login) + os.path.sep
        if not os.path.exists(directory):
            os.makedirs(directory)
        output_filepath = directory + file_name
        self.streamer_dict.update({'output_filepath': output_filepath})

        streams = streamlink.streams('https://www.twitch.tv/' + self.streamer_login)

        try:
            stream = streams[self.stream_quality]
        except KeyError:
            temp_quality = self.stream_quality
            if len(streams) > 0:  # False => stream is probably offline
                if self.stream_quality in streams.keys():
                    self.stream_quality = StreamQualities.BEST.value
                else:
                    self.stream_quality = list(streams.keys())[-1]  # best not in streams? choose best effort quality
            else:
                self.cleanup = True

            if not self.cleanup:
                print('Invalid stream quality: ' + '\'' + temp_quality + '\'')
                print('Falling back to default case: ' + self.stream_quality)
                self.streamer_dict['preferred_quality'] = self.stream_quality
                stream = streams[self.stream_quality]
            else:
                stream = None

        if not self.kill and not self.cleanup and stream:
            send_tg(f"{self.streamer} is live. Saving stream")
            print(self.streamer + ' is live. Saving stream in ' +
                  self.stream_quality + ' quality to ' + output_filepath + '.')

            try:
                with open(output_filepath, "ab") as out_file:  # open for [a]ppending as [b]inary
                    try:
                        fd = stream.open()
                    except RequestException as e:
                        send_tg(f"{self.streamer} HTTP error occurred when opening the stream: {e}")
                        print(f"HTTP error occurred when opening the stream: {e}")
                        if e.response and e.response.status_code == 404:
                            send_tg(f"{self.streamer} Stream URL not found: {e}")
                            print(f"Stream URL not found: {e}")
                        else:
                            send_tg(f"{self.streamer} HTTP error occurred: {e}")
                            print(f"HTTP error occurred: {e}")
                        self.cleanup = True
                        return

                    while not self.kill and not self.cleanup:
                        data = fd.read(1024)

                        # If data is empty the stream has ended
                        if not data:
                            fd.close()
                            out_file.close()
                            self.cleanup = True
                            break

                        out_file.write(data)
            except streamlink.StreamError as err:
                print('StreamError: {0}'.format(err))  # TODO: test when this happens
            except IOError as err:
                # If file validation fails this error gets triggered.
                print('Failed to write data to file: {0}'.format(err))
            finally:
                if fd:
                    fd.close()
            self.streamer_dict.update({'kill': self.kill})
            self.streamer_dict.update({'cleanup': self.cleanup})
            self.handle_stream_conversion()
            return self.streamer_dict

    def _formatted_download_folder(self, streamer):
        return self.download_folder.replace('#streamer#', streamer)
    
    def download_chat(self):
        send_tg(f"new chat downloading for {self.streamer}")
        chat_url = f'https://www.twitch.tv/{self.streamer_login}'
        curr_time = datetime.datetime.now().strftime("%Y-%m-%d %H.%M.%S")
        chat_output_file = self._formatted_download_folder(self.streamer_login) + os.path.sep + curr_time + " - " + self.streamer + " - " + get_valid_filename(self.stream_title) + "chat.json"

        chat = ChatDownloader().get_chat(chat_url)
        with open(chat_output_file, 'w') as f:
            for message in chat:
                if self.kill or self.cleanup:
                    break  # Exit the loop if the watcher has been signaled to stop

                simplified_message = {
                    'timestamp': message.get('timestamp'),
                    'message': message.get('message'),
                    'message_type': message.get('message_type'),
                }
                json.dump(simplified_message, f)
                f.write('\n')

    def start_chat_download(self):
        self.download_chat()

    def handle_stream_conversion(self):
        print("converting is triggered")
        if ts_file_path := self.streamer_dict.get('output_filepath'):
            send_tg(f"{self.streamer}'s stream is converting to mp4,")
            print(f"convert stream to mp4 -> {ts_file_path}")
            conversion_process = Process(target=convert_stream_to_mp4, args=(ts_file_path,))
            conversion_process.start()
            conversion_process.join()  # Wait for the conversion to finish

            # Insert video record into the database
            chat_file_path = ts_file_path.replace('.ts', 'chat.json')  # Assuming chat file has same name with 'chat.json' extension
            insert_video(ts_file_path.replace('.ts', '.mp4'), chat_file_path)