import threading

import ATRHandler
import utils
import os
from atr_cmd import AtrCmd
from daemon import Daemon

STREAMERS_FILE = 'streamers.txt'
if __name__ == '__main__':
    utils.get_client_id()  # creates necessary config before launch
    streamers_file_path = os.path.join(os.getcwd(), STREAMERS_FILE)
    server = Daemon(('127.0.0.1', 1234), ATRHandler.ATRHandler, streamers_file=streamers_file_path)
    threading.Thread(target=server.serve_forever).start()
    AtrCmd().cmdloop_with_keyboard_interrupt()
