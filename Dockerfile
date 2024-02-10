# Use the custom base image
FROM python-ffmpeg:3.8-slim

# Set the working directory
WORKDIR /usr/src/app

# Copy your application code to the container
COPY . .

# Install application dependencies
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 80

# The command to run your application
CMD ["python", "main.py"]
