#!/bin/bash

# Path to the types.py file in the streamlink package within your virtual environment
TYPES_FILE="/home/nerd/projects/twitch/myenv/lib/python3.11/site-packages/streamlink/packages/flashmedia/types.py"

# Check if the types.py file exists
if [ -f "$TYPES_FILE" ]; then
    # Use sed to replace "getattr(inspect, "getfullargspec", inspect.getargspec)" with "inspect.getfullargspec"
    sed -i 's/getattr(inspect, "getfullargspec", inspect.getargspec)/inspect.getfullargspec/g' "$TYPES_FILE"

    echo "Patch applied successfully to $TYPES_FILE"
else
    echo "Error: $TYPES_FILE does not exist. Please check the path to your virtual environment and ensure streamlink is installed."
fi

