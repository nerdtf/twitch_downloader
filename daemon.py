import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer

import ATRHandler
import twitch
from utils import get_client_id, StreamQualities
from watcher import Watcher
from tg_bot import send_tg

class Daemon(HTTPServer):
    #
    # CONSTANTS
    #
    VALID_BROADCAST = ['live']  # 'rerun' can be added through commandline flags/options
    WEBHOOK_SECRET = 'automaticTwitchRecorder'
    WEBHOOK_URL_PREFIX = 'https://api.twitch.tv/helix/streams?user_id='
    LEASE_SECONDS = 864000  # 10 days = 864000
    check_interval = 10

    def __init__(self, server_address, RequestHandlerClass, streamers_file=None):
        super().__init__(server_address, RequestHandlerClass)
        self.PORT = server_address[1]
        self.streamers = {}  # holds all streamers that need to be surveilled
        self.watched_streamers = {}  # holds all live streamers that are currently being recorded
        self.client_id = get_client_id()
        self.kill = False
        self.started = False
        self.download_folder = os.getcwd() + os.path.sep + "#streamer#"
        # ThreadPoolExecutor(max_workers): If max_workers is None or not given, it will default to the number of
        # processors on the machine, multiplied by 5
        self.pool = ThreadPoolExecutor()
        if streamers_file:
            self.load_streamers_from_file(streamers_file)

    def add_streamer(self, streamer, quality=StreamQualities.BEST.value):
        streamer = streamer.lower()
        streamer_dict = {}
        resp = []
        ok = False
        qualities = [q.value for q in StreamQualities]
        if quality not in qualities:
            resp.append('Invalid quality: ' + quality + '.')
            resp.append('Quality options: ' + str(qualities))
        else:
            streamer_dict.update({'preferred_quality': quality})

            # get channel id of streamer
            user_info = list(twitch.get_user_info(streamer))

            # check if user exists
            if user_info:
                streamer_dict.update({'user_info': user_info[0]})
                self.streamers.update({streamer: streamer_dict})
                resp.append('Successfully added ' + streamer + ' to watchlist.')
                ok = True
            else:
                resp.append('Invalid streamer name: ' + streamer + '.')
        return ok, resp

    def remove_streamer(self, streamer):
        streamer = streamer.lower()
        if streamer in self.streamers.keys():
            self.streamers.pop(streamer)
            return True, 'Removed ' + streamer + ' from watchlist.'
        elif streamer in self.watched_streamers.keys():
            watcher = self.watched_streamers[streamer]['watcher']
            watcher.quit()
            return True, 'Removed ' + streamer + ' from watchlist.'
        else:
            return False, 'Could not find ' + streamer + '. Already removed?'

    def start(self):
        if not self.started:
            self._check_streams()
            self.started = True
            return 'Daemon is started.'
        else:
            return 'Daemon is already running.'

    def set_interval(self, secs):
        if secs < 1:
            secs = 1
        self.check_interval = secs
        return 'Interval is now set to ' + str(secs) + ' seconds.'

    def set_download_folder(self, download_folder):
        self.download_folder = download_folder
        return 'Download folder is now set to \'' + download_folder + '\' .'

    def _check_streams(self):
        user_ids = [self.streamers[s]['user_info']['id'] for s in self.streamers]
        stream_info = twitch.get_stream_info(*user_ids)

        live_streamers = []

        # Process each streamer based on their status
        for streamer_name, info in self.streamers.items():
            user_id = info['user_info']['id']
            status = stream_info[user_id]
            if status['status'] == 'online':
                # Streamer is live, add to the list to start watchers
                info.update({'stream_info': status})
                live_streamers.append(streamer_name)
            elif status['status'] == 'offline':
                # Streamer went offline, handle the watcher
                if streamer_name in self.watched_streamers:
                    watcher = self.watched_streamers.pop(streamer_name)['watcher']
                    watcher.clean_break()  # Signal watcher to stop
                    watcher.quit()

        # Start watchers for live streamers
        self._start_watchers(live_streamers)

        if not self.kill:
            t = threading.Timer(self.check_interval, self._check_streams)
            t.start()



    def _start_watchers(self, live_streamers_list):
        for live_streamer in live_streamers_list:
            if live_streamer not in self.watched_streamers:
                live_streamer_dict = self.streamers.pop(live_streamer)
                curr_watcher = Watcher(live_streamer_dict, self.download_folder)

                # Submit the watch method to the thread pool and attach the callback
                t = self.pool.submit(curr_watcher.watch)
                t.add_done_callback(self._watcher_callback)

                # Submit the download_chat method to the thread pool
                self.pool.submit(curr_watcher.download_chat)

                self.watched_streamers.update({live_streamer: {'watcher': curr_watcher,
                                                            'streamer_dict': live_streamer_dict}})

    def _watcher_callback(self, returned_watcher):
        streamer_dict = returned_watcher.result()
        if streamer_dict is None:
            print("streamer_dict is None in _watcher_callback")
            return
        streamer = streamer_dict['user_info']['login']
        kill = streamer_dict['kill']
        cleanup = streamer_dict['cleanup']
        self.watched_streamers.pop(streamer)
        if not cleanup:
            print('Finished watching ' + streamer)
        else:
            output_filepath = streamer_dict['output_filepath']
            if output_filepath and os.path.exists(output_filepath):
                os.remove(output_filepath)
                print(f'Removed file: {output_filepath}')
        if not kill:
            self.add_streamer(streamer, streamer_dict['preferred_quality'])

    def get_streamers(self):
        return list(self.watched_streamers.keys()), list(self.streamers.keys())

    def exit(self):
        self.kill = True
        for streamer in self.watched_streamers.values():
            watcher = streamer['watcher']
            watcher.quit()
        self.pool.shutdown(wait=True)
        self.server_close()
        threading.Thread(target=self.shutdown, daemon=True).start()
        return 'Daemon exited successfully'
    
    def load_streamers_from_file(self, file_path):
        if not os.path.exists(file_path):
            print("Streamers file not found. Proceeding without loading streamers.")
            return

        with open(file_path, 'r') as file:
            for line in file:
                if streamer := line.strip():
                    self.add_streamer(streamer)
                    print(f"Added {streamer} to watchlist from file.")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    server = Daemon(('127.0.0.1', 1234), ATRHandler.ATRHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.exit()

    print('exited gracefully')
