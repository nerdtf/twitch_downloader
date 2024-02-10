import threading
import signal
import os
import sys

import ATRHandler
import utils
from atr_cmd import AtrCmd
from daemon import Daemon
from tg_bot import send_tg

# Global flag to indicate shutdown
shutdown_flag = threading.Event()

def signal_handler(signum, frame):
    print("Signal handler called with signal:", signum)
    # Set the shutdown flag to indicate to other threads that a shutdown has been initiated
    shutdown_flag.set()
    server.exit()

# Set up signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

STREAMERS_FILE = 'streamers.txt'

if __name__ == '__main__':
    utils.get_client_id()  # creates necessary config before launch
    streamers_file_path = os.path.join(os.getcwd(), STREAMERS_FILE)
    server = Daemon(('127.0.0.1', 1234), ATRHandler.ATRHandler, streamers_file=streamers_file_path)
    
    # Start the server in a separate thread
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.start()
    
    try:
        # Start the command loop in the main thread
        AtrCmd().cmdloop_with_keyboard_interrupt()
    finally:
        # When shutdown is initiated, stop the server
        print("Shutting down server...")
        server.shutdown()
        server.server_close()
        
        # Wait for the server thread to complete
        server_thread.join()
        print("Server has been shut down.")

    print("Main application thread exiting.")
