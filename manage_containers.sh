#!/bin/bash

# Docker Compose service name
service_name="app"
# Initial counter
counter=1
# Docker volume name
volume_name="twitch_project_data"

# Loop indefinitely
while true; do
    # Check if the Docker volume exists, if not, create it
    if [ -z "$(docker volume ls -q -f name=^${volume_name}$)" ]; then
        echo "Creating Docker volume '${volume_name}'..."
        docker volume create "${volume_name}"
    fi

    # Log the refresh count
    echo "Refresh count: $counter"

    # Rebuild the Docker image without using cache
    echo "Building the Docker image for ${service_name} without cache..."
    docker-compose build --no-cache $service_name

    # Start or recreate the service using docker-compose
    echo "Starting or updating ${service_name}..."
    docker-compose up -d --force-recreate $service_name

    # Define the duration for which to run the container (e.g., 4 hours)
    # run_duration=$((4 * 60 * 60))
    run_duration=$((60))

    # Sleep for the duration the container should run before starting a new one
    echo "Service ${service_name} is running. Waiting for ${run_duration} seconds before the next refresh..."
    sleep "${run_duration}"


     # Gracefully stop the service, allowing the application to shut down properly
    echo "Stopping ${service_name} to allow graceful shutdown..."
    docker-compose stop $service_name

    # Wait an additional hour to allow the old container to finish its tasks
    # extra_time=$((1 * 60 * 60))
    extra_time=$((30))
    echo "Waiting an additional ${extra_time} seconds to allow ongoing tasks to complete..."
    sleep "${extra_time}"

    # Increment the counter for the next refresh
    counter=$((counter + 1))
done