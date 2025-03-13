#!/bin/bash

# Function to prompt for a version number
get_version() {
    read -p "Please provide a version number: " version
    if [[ -z "$version" ]]; then
        echo "Version number is required. Exiting."
        exit 1
    fi
}

# Function to tag and push images
tag_and_push() {
    image_name="$1"
    version="$2"

    docker tag "$image_name" "italiandogs/${image_name}:$version"
    docker tag "$image_name" "italiandogs/${image_name}:latest"
    echo "Tagged $image_name with $version and latest"

    docker push "italiandogs/${image_name}:$version"
    docker push "italiandogs/${image_name}:latest"
    echo "Pushed $image_name with $version and latest"
}

# Function to optionally build Docker images
build_docker_images() {
    echo "Would you like to build the Docker images before tagging and pushing?"
    echo "1. Yes"
    echo "2. No"
    read -p "Enter your choice (1-2): " build_choice

    case "$build_choice" in
        1)
            echo "Building Docker images..."
            docker-compose -f ./config/other_configs/docker-compose.yml build
            echo "Build completed."
            ;;
        2)
            echo "Skipping build."
            ;;
        *)
            echo "Invalid choice. Skipping build."
            ;;
    esac
}

# Start script
echo "Select an option to tag and upload:"
echo "1. Bot"
echo "2. Stripe Webhook"
echo "3. Sub Manager"
echo "4. Sub Checker"
echo "5. All"
echo "0. Exit"
read -p "Enter your choice (0-5): " choice

# Collect version number and build option
get_version
build_docker_images

# Perform action based on choice
case "$choice" in
    1)
        tag_and_push "verifyme-discord-bot" "$version"
        ;;
    2)
        tag_and_push "verifyme-stripe-webhook" "$version"
        ;;
    3)
        tag_and_push "verifyme-subscription-manager" "$version"
        ;;
    4)
        tag_and_push "verifyme-subscription-checker" "$version"
        ;;
    5)
        tag_and_push "verifyme-discord-bot" "$version"
        tag_and_push "verifyme-stripe-webhook" "$version"
        tag_and_push "verifyme-subscription-manager" "$version"
        tag_and_push "verifyme-subscription-checker" "$version"
        ;;
    0)
        echo "Exiting script."
        exit 0
        ;;
    *)
        echo "Invalid choice. Exiting script."
        exit 1
        ;;
esac
